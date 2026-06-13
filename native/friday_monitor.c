/*
 * friday_monitor.c — Native system monitor daemon for FRIDAY
 *
 * Reads /proc and /sys data directly, caches results, and serves them
 * over a Unix domain socket. Python connects for instant responses.
 *
 * Commands:
 *   SYSINFO    — hostname, os, kernel, uptime, cpu, ram, disk
 *   PROCS      — top 15 processes sorted by memory usage
 *   HEALTH     — ram/disk/cpu percentages (for alert thresholds)
 *   BATTERY    — battery status, percentage, time remaining
 *   NETWORK    — interface list with IPs and status
 *   TEMPS      — CPU/thermal zone temperatures
 *   QUIT       — close connection
 *
 * Build: gcc -O2 -Wall -Wextra -pthread -o friday_monitor friday_monitor.c
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <dirent.h>
#include <signal.h>
#include <pthread.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/statvfs.h>
#include <sys/sysinfo.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <arpa/inet.h>
#include <ifaddrs.h>

#define SOCK_PATH  "/tmp/friday_monitor.sock"
#define BUF_SIZE   16384
#define MAX_PROCS  15

static pthread_mutex_t cache_lock = PTHREAD_MUTEX_INITIALIZER;

/* ---- data structures ---- */

typedef struct {
    char hostname[256];
    char os_name[256];
    char kernel[256];
    int up_days, up_hours, up_mins;
    int cpu_cores;
    double load_1, load_5;
    double cpu_pct;
    long ram_total;     /* MB */
    long ram_available; /* MB */
    long ram_used;      /* MB */
    double ram_pct;
    double disk_total;  /* GB */
    double disk_free;   /* GB */
    double disk_used;   /* GB */
    double disk_pct;
} SysCache;

typedef struct {
    int pid;
    double mem_pct;
    long rss_kb;
    char name[256];
    char cmdline[512];
} ProcInfo;

typedef struct {
    ProcInfo procs[MAX_PROCS];
    int count;
} ProcCache;

typedef struct {
    int present;
    int capacity;       /* 0-100 */
    char status[32];    /* Charging, Discharging, Full, Not charging */
    long energy_now;    /* µWh */
    long energy_full;   /* µWh */
    long power_now;     /* µW */
    double hours_left;  /* estimated */
} BatteryCache;

typedef struct {
    char name[32];
    char ipv4[64];
    char state[16];     /* up/down */
    long rx_bytes;
    long tx_bytes;
} NetIface;

typedef struct {
    NetIface ifaces[16];
    int count;
} NetworkCache;

typedef struct {
    char zone[64];
    double temp_c;
} ThermalZone;

typedef struct {
    ThermalZone zones[8];
    int count;
} TempCache;

static SysCache     sys_cache;
static ProcCache    proc_cache;
static BatteryCache bat_cache;
static NetworkCache net_cache;
static TempCache    temp_cache;
static volatile int running = 1;

/* ---- /proc readers ---- */

static void read_hostname(SysCache *c) {
    FILE *f = fopen("/etc/hostname", "r");
    if (f) {
        if (fgets(c->hostname, sizeof(c->hostname), f))
            c->hostname[strcspn(c->hostname, "\n")] = 0;
        fclose(f);
    }
}

static void read_os_name(SysCache *c) {
    FILE *f = fopen("/etc/os-release", "r");
    if (!f) return;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "PRETTY_NAME=", 12) == 0) {
            char *s = strchr(line, '"');
            if (s) {
                s++;
                char *e = strchr(s, '"');
                if (e) *e = 0;
                strncpy(c->os_name, s, sizeof(c->os_name) - 1);
            }
            break;
        }
    }
    fclose(f);
}

static void read_kernel(SysCache *c) {
    FILE *f = fopen("/proc/version", "r");
    if (!f) return;
    char buf[512];
    if (fgets(buf, sizeof(buf), f)) {
        char *tok = strtok(buf, " ");
        tok = strtok(NULL, " ");
        tok = strtok(NULL, " ");
        if (tok) strncpy(c->kernel, tok, sizeof(c->kernel) - 1);
    }
    fclose(f);
}

static void read_uptime(SysCache *c) {
    FILE *f = fopen("/proc/uptime", "r");
    if (!f) return;
    double secs;
    if (fscanf(f, "%lf", &secs) == 1) {
        int total_mins = (int)(secs / 60);
        c->up_mins  = total_mins % 60;
        int total_hrs = total_mins / 60;
        c->up_hours = total_hrs % 24;
        c->up_days  = total_hrs / 24;
    }
    fclose(f);
}

static void read_cpu(SysCache *c) {
    c->cpu_cores = sysconf(_SC_NPROCESSORS_ONLN);
    if (c->cpu_cores < 1) c->cpu_cores = 1;

    FILE *f = fopen("/proc/loadavg", "r");
    if (!f) return;
    fscanf(f, "%lf %lf", &c->load_1, &c->load_5);
    fclose(f);

    c->cpu_pct = (c->load_1 / c->cpu_cores) * 100.0;
    if (c->cpu_pct > 100.0) c->cpu_pct = 100.0;
}

static void read_ram(SysCache *c) {
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return;
    char line[256];
    long total_kb = 0, avail_kb = 0;
    int found = 0;
    while (fgets(line, sizeof(line), f) && found < 2) {
        if (strncmp(line, "MemTotal:", 9) == 0) {
            sscanf(line + 9, "%ld", &total_kb); found++;
        } else if (strncmp(line, "MemAvailable:", 13) == 0) {
            sscanf(line + 13, "%ld", &avail_kb); found++;
        }
    }
    fclose(f);
    c->ram_total     = total_kb / 1024;
    c->ram_available = avail_kb / 1024;
    c->ram_used      = c->ram_total - c->ram_available;
    c->ram_pct       = (c->ram_total > 0) ? ((double)c->ram_used / c->ram_total * 100.0) : 0;
}

static void read_disk(SysCache *c) {
    struct statvfs st;
    if (statvfs("/", &st) == 0) {
        c->disk_total = (double)(st.f_blocks * st.f_frsize) / (1024.0*1024.0*1024.0);
        c->disk_free  = (double)(st.f_bavail * st.f_frsize) / (1024.0*1024.0*1024.0);
        c->disk_used  = c->disk_total - c->disk_free;
        c->disk_pct   = (c->disk_total > 0) ? (c->disk_used / c->disk_total * 100.0) : 0;
    }
}

/* ---- battery from /sys ---- */

static long read_sys_long(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    long val;
    if (fscanf(f, "%ld", &val) != 1) val = -1;
    fclose(f);
    return val;
}

static void read_sys_str(const char *path, char *buf, int sz) {
    FILE *f = fopen(path, "r");
    if (!f) { buf[0] = 0; return; }
    if (fgets(buf, sz, f)) buf[strcspn(buf, "\n")] = 0;
    else buf[0] = 0;
    fclose(f);
}

static void read_battery(BatteryCache *b) {
    /* try BAT0, then BAT1 */
    const char *bats[] = {"/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1", NULL};
    b->present = 0;

    for (int i = 0; bats[i]; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s/capacity", bats[i]);
        long cap = read_sys_long(path);
        if (cap >= 0) {
            b->present = 1;
            b->capacity = (int)cap;

            snprintf(path, sizeof(path), "%s/status", bats[i]);
            read_sys_str(path, b->status, sizeof(b->status));

            snprintf(path, sizeof(path), "%s/energy_now", bats[i]);
            b->energy_now = read_sys_long(path);

            snprintf(path, sizeof(path), "%s/energy_full", bats[i]);
            b->energy_full = read_sys_long(path);

            snprintf(path, sizeof(path), "%s/power_now", bats[i]);
            b->power_now = read_sys_long(path);

            /* charge_now / charge_full fallback (some laptops) */
            if (b->energy_now < 0) {
                snprintf(path, sizeof(path), "%s/charge_now", bats[i]);
                b->energy_now = read_sys_long(path);
                snprintf(path, sizeof(path), "%s/charge_full", bats[i]);
                b->energy_full = read_sys_long(path);
            }

            if (b->power_now > 0 && b->energy_now > 0) {
                b->hours_left = (double)b->energy_now / b->power_now;
            } else {
                b->hours_left = -1;
            }
            break;
        }
    }
}

/* ---- network from /sys/class/net + getifaddrs ---- */

static void read_network(NetworkCache *nc) {
    nc->count = 0;
    struct ifaddrs *ifaddr, *ifa;

    if (getifaddrs(&ifaddr) == -1) return;

    for (ifa = ifaddr; ifa && nc->count < 16; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET) continue;
        if (strcmp(ifa->ifa_name, "lo") == 0) continue;

        NetIface *n = &nc->ifaces[nc->count];
        strncpy(n->name, ifa->ifa_name, sizeof(n->name) - 1);

        struct sockaddr_in *sa = (struct sockaddr_in *)ifa->ifa_addr;
        inet_ntop(AF_INET, &sa->sin_addr, n->ipv4, sizeof(n->ipv4));

        /* check if up */
        if (ifa->ifa_flags & IFF_UP)
            strcpy(n->state, "UP");
        else
            strcpy(n->state, "DOWN");

        /* read rx/tx from /sys */
        char spath[256];
        snprintf(spath, sizeof(spath), "/sys/class/net/%s/statistics/rx_bytes", n->name);
        n->rx_bytes = read_sys_long(spath);
        snprintf(spath, sizeof(spath), "/sys/class/net/%s/statistics/tx_bytes", n->name);
        n->tx_bytes = read_sys_long(spath);

        nc->count++;
    }
    freeifaddrs(ifaddr);
}

/* ---- thermal from /sys/class/thermal ---- */

static void read_temps(TempCache *tc) {
    tc->count = 0;
    char path[256];

    for (int i = 0; i < 8; i++) {
        snprintf(path, sizeof(path), "/sys/class/thermal/thermal_zone%d/temp", i);
        long temp_mc = read_sys_long(path);
        if (temp_mc < 0) break;

        ThermalZone *z = &tc->zones[tc->count];
        z->temp_c = temp_mc / 1000.0;

        snprintf(path, sizeof(path), "/sys/class/thermal/thermal_zone%d/type", i);
        read_sys_str(path, z->zone, sizeof(z->zone));
        if (z->zone[0] == 0) snprintf(z->zone, sizeof(z->zone), "zone%d", i);

        tc->count++;
    }
}

/* ---- process reader ---- */

static int proc_cmp(const void *a, const void *b) {
    return ((const ProcInfo *)b)->rss_kb - ((const ProcInfo *)a)->rss_kb > 0 ? 1 : -1;
}

static void read_processes(ProcCache *pc) {
    DIR *d = opendir("/proc");
    if (!d) return;

    ProcInfo all[512];
    int total = 0;
    struct dirent *entry;

    while ((entry = readdir(d)) && total < 512) {
        int pid = atoi(entry->d_name);
        if (pid <= 0) continue;

        ProcInfo *p = &all[total];
        p->pid = pid;
        p->rss_kb = 0;
        p->mem_pct = 0;
        p->name[0] = 0;
        p->cmdline[0] = 0;

        char path[256];
        snprintf(path, sizeof(path), "/proc/%d/status", pid);
        FILE *f = fopen(path, "r");
        if (!f) continue;

        char line[512];
        while (fgets(line, sizeof(line), f)) {
            if (strncmp(line, "Name:", 5) == 0)
                sscanf(line + 5, " %255s", p->name);
            else if (strncmp(line, "VmRSS:", 6) == 0)
                sscanf(line + 6, " %ld", &p->rss_kb);
        }
        fclose(f);

        snprintf(path, sizeof(path), "/proc/%d/cmdline", pid);
        f = fopen(path, "r");
        if (f) {
            size_t n = fread(p->cmdline, 1, sizeof(p->cmdline) - 1, f);
            p->cmdline[n] = 0;
            for (size_t j = 0; j < n; j++)
                if (p->cmdline[j] == 0) p->cmdline[j] = ' ';
            fclose(f);
        }

        if (p->rss_kb > 0) total++;
    }
    closedir(d);

    qsort(all, total, sizeof(ProcInfo), proc_cmp);

    long total_ram_kb = 0;
    FILE *mf = fopen("/proc/meminfo", "r");
    if (mf) {
        char line[256];
        while (fgets(line, sizeof(line), mf))
            if (strncmp(line, "MemTotal:", 9) == 0) { sscanf(line+9, "%ld", &total_ram_kb); break; }
        fclose(mf);
    }

    pc->count = (total < MAX_PROCS) ? total : MAX_PROCS;
    for (int i = 0; i < pc->count; i++) {
        pc->procs[i] = all[i];
        if (total_ram_kb > 0)
            pc->procs[i].mem_pct = (double)all[i].rss_kb / total_ram_kb * 100.0;
    }
}

/* ---- background refresh thread ---- */

static void *refresh_thread(void *arg) {
    (void)arg;

    /* one-time static reads */
    pthread_mutex_lock(&cache_lock);
    read_hostname(&sys_cache);
    read_os_name(&sys_cache);
    read_kernel(&sys_cache);
    pthread_mutex_unlock(&cache_lock);

    while (running) {
        SysCache     tmp_sys;
        ProcCache    tmp_proc;
        BatteryCache tmp_bat;
        NetworkCache tmp_net;
        TempCache    tmp_temp;

        memcpy(&tmp_sys, &sys_cache, sizeof(SysCache));
        read_uptime(&tmp_sys);
        read_cpu(&tmp_sys);
        read_ram(&tmp_sys);
        read_disk(&tmp_sys);
        read_processes(&tmp_proc);
        read_battery(&tmp_bat);
        read_network(&tmp_net);
        read_temps(&tmp_temp);

        pthread_mutex_lock(&cache_lock);
        memcpy(&sys_cache,  &tmp_sys,  sizeof(SysCache));
        memcpy(&proc_cache, &tmp_proc, sizeof(ProcCache));
        memcpy(&bat_cache,  &tmp_bat,  sizeof(BatteryCache));
        memcpy(&net_cache,  &tmp_net,  sizeof(NetworkCache));
        memcpy(&temp_cache, &tmp_temp, sizeof(TempCache));
        pthread_mutex_unlock(&cache_lock);

        sleep(2);
    }
    return NULL;
}

/* ---- response formatters ---- */

static int format_sysinfo(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    SysCache c = sys_cache;
    BatteryCache b = bat_cache;
    pthread_mutex_unlock(&cache_lock);

    int off = snprintf(buf, sz,
        "Hostname: %s\n"
        "OS: %s\n"
        "Kernel: %s\n"
        "Uptime: %dd %dh %dm\n"
        "CPU: %d cores, load %.2f/%.2f (%.1f%%)\n"
        "RAM: %ld MB / %ld MB (%.1f%%)\n"
        "Disk (/): %.1f GB / %.1f GB (%.1f%%)\n",
        c.hostname, c.os_name, c.kernel,
        c.up_days, c.up_hours, c.up_mins,
        c.cpu_cores, c.load_1, c.load_5, c.cpu_pct,
        c.ram_used, c.ram_total, c.ram_pct,
        c.disk_used, c.disk_total, c.disk_pct
    );

    if (b.present) {
        off += snprintf(buf + off, sz - off, "Battery: %d%% (%s)", b.capacity, b.status);
        if (b.hours_left > 0 && strcmp(b.status, "Discharging") == 0)
            off += snprintf(buf + off, sz - off, " ~%.1fh remaining", b.hours_left);
        off += snprintf(buf + off, sz - off, "\n");
    }
    return off;
}

static int format_procs(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    ProcCache pc = proc_cache;
    pthread_mutex_unlock(&cache_lock);

    int off = snprintf(buf, sz, "PID      MEM%%    RSS(MB)  NAME            COMMAND\n");
    for (int i = 0; i < pc.count && off < sz - 200; i++) {
        ProcInfo *p = &pc.procs[i];
        char cmd_s[80];
        strncpy(cmd_s, p->cmdline, 75);
        cmd_s[75] = 0;
        if (strlen(p->cmdline) > 75) strcat(cmd_s, "...");
        off += snprintf(buf + off, sz - off,
            "%-8d %5.1f%%  %7ld  %-15s %s\n",
            p->pid, p->mem_pct, p->rss_kb / 1024, p->name, cmd_s);
    }
    return off;
}

static int format_health(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    SysCache c = sys_cache;
    BatteryCache b = bat_cache;
    pthread_mutex_unlock(&cache_lock);

    int off = snprintf(buf, sz,
        "ram_pct=%.1f\nram_used=%ld\nram_total=%ld\n"
        "disk_pct=%.1f\ndisk_used=%.1f\ndisk_total=%.1f\n"
        "cpu_pct=%.1f\nload=%.2f\n",
        c.ram_pct, c.ram_used, c.ram_total,
        c.disk_pct, c.disk_used, c.disk_total,
        c.cpu_pct, c.load_1);

    if (b.present)
        off += snprintf(buf + off, sz - off,
            "bat_pct=%d\nbat_status=%s\n", b.capacity, b.status);
    return off;
}

static int format_battery(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    BatteryCache b = bat_cache;
    pthread_mutex_unlock(&cache_lock);

    if (!b.present)
        return snprintf(buf, sz, "No battery detected (desktop or AC-only).\n");

    int off = snprintf(buf, sz,
        "Battery: %d%%\nStatus: %s\n", b.capacity, b.status);

    if (b.energy_now > 0 && b.energy_full > 0)
        off += snprintf(buf + off, sz - off,
            "Energy: %.1f / %.1f Wh\n",
            b.energy_now / 1e6, b.energy_full / 1e6);

    if (b.power_now > 0)
        off += snprintf(buf + off, sz - off,
            "Power draw: %.1f W\n", b.power_now / 1e6);

    if (b.hours_left > 0 && strcmp(b.status, "Discharging") == 0)
        off += snprintf(buf + off, sz - off,
            "Time remaining: ~%.1f hours\n", b.hours_left);

    return off;
}

static int format_network(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    NetworkCache nc = net_cache;
    pthread_mutex_unlock(&cache_lock);

    if (nc.count == 0)
        return snprintf(buf, sz, "No active network interfaces found.\n");

    int off = snprintf(buf, sz, "INTERFACE    STATE  IP               RX(MB)    TX(MB)\n");
    for (int i = 0; i < nc.count && off < sz - 100; i++) {
        NetIface *n = &nc.ifaces[i];
        off += snprintf(buf + off, sz - off,
            "%-12s %-5s  %-15s  %7.1f   %7.1f\n",
            n->name, n->state, n->ipv4,
            (n->rx_bytes > 0) ? n->rx_bytes / 1e6 : 0,
            (n->tx_bytes > 0) ? n->tx_bytes / 1e6 : 0);
    }
    return off;
}

static int format_temps(char *buf, int sz) {
    pthread_mutex_lock(&cache_lock);
    TempCache tc = temp_cache;
    pthread_mutex_unlock(&cache_lock);

    if (tc.count == 0)
        return snprintf(buf, sz, "No thermal sensors found.\n");

    int off = snprintf(buf, sz, "ZONE                 TEMP\n");
    for (int i = 0; i < tc.count && off < sz - 60; i++) {
        ThermalZone *z = &tc.zones[i];
        off += snprintf(buf + off, sz - off,
            "%-20s %.1f°C\n", z->zone, z->temp_c);
    }
    return off;
}

/* ---- client handler ---- */

static void *handle_client(void *arg) {
    int fd = *(int *)arg;
    free(arg);

    char cmd[64];
    char response[BUF_SIZE];

    while (1) {
        memset(cmd, 0, sizeof(cmd));
        ssize_t n = recv(fd, cmd, sizeof(cmd) - 1, 0);
        if (n <= 0) break;

        cmd[strcspn(cmd, "\r\n")] = 0;

        if (strcmp(cmd, "SYSINFO") == 0)
            format_sysinfo(response, sizeof(response));
        else if (strcmp(cmd, "PROCS") == 0)
            format_procs(response, sizeof(response));
        else if (strcmp(cmd, "HEALTH") == 0)
            format_health(response, sizeof(response));
        else if (strcmp(cmd, "BATTERY") == 0)
            format_battery(response, sizeof(response));
        else if (strcmp(cmd, "NETWORK") == 0)
            format_network(response, sizeof(response));
        else if (strcmp(cmd, "TEMPS") == 0)
            format_temps(response, sizeof(response));
        else if (strcmp(cmd, "QUIT") == 0)
            break;
        else
            snprintf(response, sizeof(response), "ERR: unknown '%s'\n", cmd);

        send(fd, response, strlen(response), 0);
    }

    close(fd);
    return NULL;
}

/* ---- signal handler ---- */

static void sig_handler(int sig) {
    (void)sig;
    running = 0;
}

/* ---- main ---- */

int main(void) {
    signal(SIGINT,  sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGPIPE, SIG_IGN);

    unlink(SOCK_PATH);

    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) { perror("socket"); return 1; }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCK_PATH, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(server_fd); return 1;
    }

    if (listen(server_fd, 5) < 0) {
        perror("listen"); close(server_fd); return 1;
    }

    printf("[friday_monitor] pid=%d sock=%s\n", getpid(), SOCK_PATH);
    printf("[friday_monitor] commands: SYSINFO PROCS HEALTH BATTERY NETWORK TEMPS QUIT\n");
    fflush(stdout);

    /* fast initial reads so first SYSINFO/HEALTH/BATTERY queries work instantly */
    read_hostname(&sys_cache);
    read_os_name(&sys_cache);
    read_kernel(&sys_cache);
    read_uptime(&sys_cache);
    read_cpu(&sys_cache);
    read_ram(&sys_cache);
    read_disk(&sys_cache);
    read_battery(&bat_cache);
    read_network(&net_cache);
    read_temps(&temp_cache);
    /* process scan is slow (~2-3s for 500+ PIDs) — done in background thread */

    /* start background refresh (first iteration does process scan) */
    pthread_t refresh_tid;
    pthread_create(&refresh_tid, NULL, refresh_thread, NULL);

    while (running) {
        int *client_fd = malloc(sizeof(int));
        *client_fd = accept(server_fd, NULL, NULL);
        if (*client_fd < 0) {
            free(client_fd);
            if (errno == EINTR) continue;
            break;
        }
        pthread_t tid;
        pthread_create(&tid, NULL, handle_client, client_fd);
        pthread_detach(tid);
    }

    printf("[friday_monitor] shutting down\n");
    close(server_fd);
    unlink(SOCK_PATH);
    running = 0;
    pthread_join(refresh_tid, NULL);

    return 0;
}
