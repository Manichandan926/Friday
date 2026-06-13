CC      = gcc
CFLAGS  = -O2 -Wall -Wextra
SOFLAGS = -shared -fPIC -O3
TARGET  = friday_monitor
SRC     = friday_monitor.c

# shared library modules
LIBS    = command_validator.so

.PHONY: all clean install libs

all: $(TARGET) $(LIBS)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -pthread -o $@ $<

command_validator.so: command_validator.c
	$(CC) $(SOFLAGS) -o $@ $<

libs: $(LIBS)

clean:
	rm -f $(TARGET) *.so

install: $(TARGET)
	@echo "Copying binary to ~/.local/bin/"
	mkdir -p $(HOME)/.local/bin
	cp $(TARGET) $(HOME)/.local/bin/
	@echo "Done. Make sure ~/.local/bin is in your PATH."
