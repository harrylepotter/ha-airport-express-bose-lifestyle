/*
 * Configure the examined PL2303 HX via endpoint zero; open/close raw bulk pipes.
 * For the A1392 7.6.2 ulpt_match patch. Standalone diagnostics require the
 * Bose DISCONNECTED; the library entry point serves the normal controller.
 * Standalone default sends no serial payload. --loopback sends one fixed 16-byte
 * pattern and checks its echo; use only after joining this adapter's TX/RX.
 * No EEPROM writes, USB device reset or firmware operations.
 * Control sequence reference: Linux drivers/usb/serial/pl2303.c (TYPE_HX).
 */
#include <sys/types.h>
#include <sys/ioctl.h>
#include <dev/usb/usb.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef char request_abi[USB_REQUEST == 0xc0185501UL ? 1 : -1];
typedef char deviceinfo_abi[USB_DEVICEINFO == 0xc4f45504UL ? 1 : -1];

static int control(int fd, int addr, unsigned type, unsigned request,
                   unsigned value, unsigned index, void *data, unsigned size)
{
    struct usb_ctl_request req;
    memset(&req, 0, sizeof(req));
    req.ucr_addr = addr;
    req.ucr_request.bmRequestType = type;
    req.ucr_request.bRequest = request;
    USETW(req.ucr_request.wValue, value);
    USETW(req.ucr_request.wIndex, index);
    USETW(req.ucr_request.wLength, size);
    req.ucr_data = data;
    if (ioctl(fd, USB_REQUEST, &req) == -1 || req.ucr_actlen != (int)size) {
        fprintf(stderr, "Control %02x/%02x value %04x index %04x failed: %s (length %d/%u)\n",
                type, request, value, index, strerror(errno), req.ucr_actlen, size);
        return -1;
    }
    return 0;
}

static int vendor(int fd, int addr, int reading, unsigned value, unsigned index)
{
    unsigned char byte;
    return control(fd, addr, reading ? 0xc0 : 0x40, 1, value, index,
                   reading ? &byte : NULL, reading ? 1 : 0);
}

#ifndef AIRPORT_PL2303_LIBRARY
static void expired(int signo)
{
    static const char message[] = "Loopback deadline reached; no retry\n";
    (void)signo;
    (void)write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(124);
}

static int loopback(int bulk)
{
    static const unsigned char sent[] = {
        0x55, 0xaa, 0x00, 0xff, 0x13, 0x11, 0x42, 0x81,
        0x7e, 0xe7, 0x01, 0x80, 0x0d, 0x0a, 0x5a, 0xa5
    };
    unsigned char received[sizeof(sent)];
    struct sigaction sa;
    size_t got = 0, i;
    ssize_t count;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = expired;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGALRM, &sa, NULL) == -1) { perror("alarm handler"); return 1; }
    /* Apple's usbd_bulk_transfer uses PCATCH and aborts the pipe on signals. */
    alarm(5);
    count = write(bulk, sent, sizeof(sent));
    if (count != sizeof(sent)) {
        alarm(0);
        fprintf(stderr, "Bulk write returned %ld/16: %s; no retry\n", (long)count, strerror(errno));
        return 1;
    }
    puts("USB bulk OUT accepted 16 bytes; awaiting their UART loopback echo...");
    while (got < sizeof(received)) {
        count = read(bulk, received + got, sizeof(received) - got);
        if (count <= 0) {
            alarm(0);
            fprintf(stderr, "Bulk read failed after %lu/16 bytes: %s\n", (unsigned long)got,
                    count < 0 ? strerror(errno) : "end of stream");
            return 1;
        }
        got += (size_t)count;
    }
    alarm(0);
    printf("Received:");
    for (i = 0; i < sizeof(received); ++i) printf(" %02x", received[i]);
    putchar('\n');
    if (memcmp(sent, received, sizeof(sent))) {
        fprintf(stderr, "FAIL: loopback bytes differ from transmitted pattern\n");
        return 1;
    }
    puts("PASS: all 16 bytes looped through UART TX/RX at 1200/8N1 exactly");
    return 0;
}
#endif

/* Caller owns returned O_RDWR descriptor. No serial payload is sent here. */
int airport_pl2303_open_bulk(void)
{
    static const struct { int read; unsigned value, index; } startup[] = {
        {1,0x8484,0}, {0,0x0404,0}, {1,0x8484,0}, {1,0x8383,0},
        {1,0x8484,0}, {0,0x0404,1}, {1,0x8484,0}, {1,0x8383,0},
        {0,0,1}, {0,1,0}, {0,2,0x44}, {0,8,0}, {0,9,0}
    };
    unsigned char descriptor[18], line[7] = {0xb0,0x04,0,0,0,0,8}, readback[7], flow;
    struct usb_device_info di;
    int fd, bulk, addr, found = 0, driver = 0, i;
    unsigned n;
    fd = open("/dev/usb0", O_RDWR);
    if (fd < 0) { perror("open usb0"); return -1; }
    for (addr = 1; addr < USB_MAX_DEVICES; ++addr) {
        memset(&di, 0, sizeof(di));
        di.udi_addr = addr;
        if (ioctl(fd, USB_DEVICEINFO, &di) == -1) {
            if (errno != ENXIO && errno != EINVAL) { perror("device info"); goto fail; }
            continue;
        }
        if (di.udi_vendorNo != 0x067b || di.udi_productNo != 0x2303)
            continue;
        if (found) { fprintf(stderr, "More than one PL2303 attached; stop\n"); goto fail; }
        found = addr;
        for (i = 0; i < USB_MAX_DEVNAMES; ++i)
            if (!strncmp(di.udi_devnames[i], "ulpt0", sizeof(di.udi_devnames[i]))) driver = 1;
    }
    if (!found || !driver) { fprintf(stderr, "Expected PL2303 bound to ulpt0 is absent\n"); goto fail; }
    if (control(fd, found, 0x80, 6, 0x0100, 0, descriptor, sizeof(descriptor))) goto fail;
    if (descriptor[0] != 18 || descriptor[1] != 1 || descriptor[2] != 0x10 ||
        descriptor[3] != 1 || descriptor[4] != 0 || descriptor[7] != 64 ||
        descriptor[8] != 0x7b || descriptor[9] != 0x06 || descriptor[10] != 3 ||
        descriptor[11] != 0x23 || descriptor[12] != 0 || descriptor[13] != 3 ||
        descriptor[17] != 1) {
        fprintf(stderr, "Device is not the examined USB 1.1 PL2303 HX revision 0300\n");
        goto fail;
    }
    printf("PL2303 HX at USB address %d is attached to ulpt0\n", found);
    for (n = 0; n < sizeof(startup) / sizeof(startup[0]); ++n)
        if (vendor(fd, found, startup[n].read, startup[n].value, startup[n].index)) goto fail;
    puts("HX startup and data-pipe initialization accepted");
    if (control(fd, found, 0x21, 0x20, 0, 0, line, sizeof(line))) goto fail;
    if (control(fd, found, 0xa1, 0x21, 0, 0, readback, sizeof(readback))) goto fail;
    if (memcmp(line, readback, sizeof(line))) {
        fprintf(stderr, "1200/8N1 line-setting read-back mismatch\n"); goto fail;
    }
    puts("PASS: line coding read-back is exactly 1200 baud, 8 data bits, no parity, 1 stop bit");
    if (control(fd, found, 0xc0, 1, 0x80, 0, &flow, 1)) goto fail;
    if (vendor(fd, found, 0, 0, flow & 0x0f)) goto fail;
    if (control(fd, found, 0xc0, 1, 0x80, 0, &flow, 1)) goto fail;
    if (flow & 0xf0) { fprintf(stderr, "Flow-control read-back mismatch\n"); goto fail; }
    puts("PASS: hardware/software flow control disabled and read back");
    /* Three-wire Bose cable does not use DTR/RTS; leave both deasserted. */
    if (control(fd, found, 0x21, 0x22, 0, 0, NULL, 0)) goto fail;
    bulk = open("/dev/ulpt0", O_RDWR);
    if (bulk < 0) { perror("open ulpt0 read/write bulk pipes"); goto fail; }
    close(fd);
    return bulk;
fail:
    close(fd);
    return -1;
}

#ifndef AIRPORT_PL2303_LIBRARY
int main(int argc, char **argv)
{
    int bulk, testing;
    if ((argc != 2 && argc != 3) || strcmp(argv[1], "--bose-disconnected") ||
        (argc == 3 && strcmp(argv[2], "--loopback"))) {
        fprintf(stderr, "usage: %s --bose-disconnected [--loopback]\n", argv[0]);
        return 2;
    }
    testing = argc == 3;
    setvbuf(stdout, NULL, _IONBF, 0);
    bulk = airport_pl2303_open_bulk();
    if (bulk < 0) return 1;
    if (testing && loopback(bulk)) { close(bulk); return 1; }
    if (close(bulk) == -1) { perror("close ulpt0"); return 1; }
    puts(testing ? "Bulk pipes closed after loopback test" :
         "PASS: raw bulk IN/OUT pipes opened and closed; zero serial payload bytes sent");
    return 0;
}
#endif
