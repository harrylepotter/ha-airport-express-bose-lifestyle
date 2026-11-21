#!/bin/sh
# Run inside the cross-compiler container with read-only /source and /sysroot.
# NetBSD 4.0 sgimips libc/headers, not the compiler's Linux libc.
set -eu
lib=/sysroot/usr/lib
airport_source=${1:-airport-usb-probe.c}
airport_output=${2:-airport-usb-probe}
if [ "$#" -ge 1 ]; then shift; fi
if [ "$#" -ge 1 ]; then shift; fi
mips-linux-gnu-gcc \
    -EB -mabi=32 -march=mips1 -mtune=24kc -static -no-pie \
    -Wl,--build-id=none -Wl,-e,__start \
    -msoft-float -mabicalls -fPIC -fgnu89-inline -O2 -Wall -Wextra -Werror \
    -nostdinc -isystem /sysroot/usr/include -nostdlib \
    -o "/build/$airport_output" \
    "$lib/crt0.o" "$lib/crti.o" "$lib/crtbeginT.o" \
    "/source/$airport_source" \
    "$@" \
    -L"$lib" -Wl,--start-group -lc -lgcc -Wl,--end-group \
    "$lib/crtend.o" "$lib/crtn.o"
mips-linux-gnu-readelf -h -l -n "/build/$airport_output"
sha256sum "/build/$airport_output"
