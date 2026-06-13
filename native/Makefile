CC      = gcc
CFLAGS  = -O2 -Wall -Wextra -pthread
TARGET  = friday_monitor
SRC     = friday_monitor.c

.PHONY: all clean install

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)

install: $(TARGET)
	@echo "Copying binary to ~/.local/bin/"
	mkdir -p $(HOME)/.local/bin
	cp $(TARGET) $(HOME)/.local/bin/
	@echo "Done. Make sure ~/.local/bin is in your PATH."
