/*
 * command_validator.c — Fast shell command validation for FRIDAY
 *
 * Replaces Python regex-based validation with:
 *   - Sorted whitelist + binary search for O(log n) command lookup
 *   - strstr-based banned pattern detection (no regex overhead)
 *
 * Build: gcc -shared -fPIC -O3 -o command_validator.so command_validator.c
 *
 * Python usage:
 *   from app.core.fast_ops import is_command_safe_native
 *   safe, reason = is_command_safe_native("ps aux | grep python")
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* sorted whitelist for binary search */
static const char *WHITELIST[] = {
    "acpi",       "awk",        "cal",        "cargo",      "cat",
    "cut",        "date",       "df",         "dig",        "dmidecode",
    "du",         "echo",       "env",        "fastfetch",  "file",
    "find",       "free",       "g++",        "gcc",        "git",
    "go",         "grep",       "head",       "hostname",   "hostnamectl",
    "htop",       "ifconfig",   "iostat",     "ip",         "iw",
    "iwconfig",   "java",       "javac",      "journalctl", "loginctl",
    "ls",         "lsblk",      "lscpu",      "lshw",       "lsmem",
    "lsof",       "lspci",      "lsusb",      "md5sum",     "mkdir",
    "neofetch",   "netstat",    "nmcli",      "node",       "npm",
    "nslookup",   "pgrep",      "ping",       "pip",        "pip3",
    "pmap",       "printenv",   "ps",         "pwd",        "python",     "python3",
    "readlink",   "realpath",   "rustc",      "sar",        "screenfetch",
    "sed",        "sensors",    "sha256sum",  "sleep",      "sort",       "ss",
    "stat",       "strace",     "strings",    "systemctl",  "tail",
    "tee",        "timedatectl","top",        "touch",      "tr",
    "traceroute", "type",       "uname",      "uniq",       "upower",
    "uptime",     "vmstat",     "wc",         "whereis",    "which",
    "who",        "whoami",     "xargs",
};

static const int WHITELIST_SIZE = sizeof(WHITELIST) / sizeof(WHITELIST[0]);

/* banned substrings — checked via strstr, no regex needed */
static const char *BANNED_PATTERNS[] = {
    "sudo ",   "sudo\t",  " su ",    "\tsu ",
    " rm ",    "\trm ",   " rm\t",   " dd ",
    "mkfs",    "fdisk",   "chmod ",  "chown ",
    " kill ",  "killall", "pkill",
    "reboot",  "shutdown","poweroff","init ",
    "> /",     ">> /",    ">/",      ">>/",
    "| sh",    "| bash",  "|sh",     "|bash",
    "eval ",   "exec ",   ":(){",
    "format",  "nc -e",   "nc -l",
    "/dev/sd", "/dev/nvme",
    "passwd",  "useradd", "userdel",
    "mv /",    "cp /",    "mkdir -p /",
};

static const int BANNED_SIZE = sizeof(BANNED_PATTERNS) / sizeof(BANNED_PATTERNS[0]);

/* binary search on sorted whitelist */
static int in_whitelist(const char *cmd) {
    int lo = 0, hi = WHITELIST_SIZE - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        int cmp = strcmp(cmd, WHITELIST[mid]);
        if (cmp == 0) return 1;
        if (cmp < 0) hi = mid - 1;
        else lo = mid + 1;
    }
    return 0;
}

/* check banned patterns using strstr */
static int has_banned_pattern(const char *cmd_lower) {
    for (int i = 0; i < BANNED_SIZE; i++) {
        if (strstr(cmd_lower, BANNED_PATTERNS[i]) != NULL)
            return 1;
    }
    return 0;
}

/* extract base command name from a segment (strip path, leading whitespace) */
static void extract_base_cmd(const char *segment, char *out, int out_sz) {
    /* skip leading whitespace */
    while (*segment && isspace(*segment)) segment++;

    /* find end of first token */
    const char *end = segment;
    while (*end && !isspace(*end)) end++;

    /* find last '/' for path stripping */
    const char *slash = NULL;
    for (const char *p = segment; p < end; p++) {
        if (*p == '/') slash = p;
    }
    if (slash) segment = slash + 1;

    int len = (int)(end - segment);
    if (len >= out_sz) len = out_sz - 1;
    memcpy(out, segment, len);
    out[len] = '\0';
}

/*
 * validate_command — main entry point called from Python via ctypes
 *
 * Returns:
 *   1 = safe to execute
 *   0 = blocked
 *
 * reason_buf is filled with the block reason (empty string if safe)
 */
int validate_command(const char *command, char *reason_buf, int reason_sz) {
    if (!command || !command[0]) {
        snprintf(reason_buf, reason_sz, "Empty command.");
        return 0;
    }

    /* lowercase copy for pattern matching */
    int cmd_len = strlen(command);
    if (cmd_len > 4096) {
        snprintf(reason_buf, reason_sz, "Command too long.");
        return 0;
    }

    char lower[4097];
    for (int i = 0; i <= cmd_len; i++)
        lower[i] = tolower((unsigned char)command[i]);

    /* check banned patterns */
    if (has_banned_pattern(lower)) {
        snprintf(reason_buf, reason_sz, "Blocked: matches dangerous pattern.");
        return 0;
    }

    /* split on pipe and validate each segment */
    char copy[4097];
    memcpy(copy, command, cmd_len + 1);

    char *saveptr = NULL;
    char *seg = strtok_r(copy, "|", &saveptr);
    char base[256];

    while (seg) {
        extract_base_cmd(seg, base, sizeof(base));

        if (base[0] && !in_whitelist(base)) {
            snprintf(reason_buf, reason_sz,
                     "Command '%s' is not in the safe list.", base);
            return 0;
        }

        seg = strtok_r(NULL, "|", &saveptr);
    }

    reason_buf[0] = '\0';
    return 1;
}
