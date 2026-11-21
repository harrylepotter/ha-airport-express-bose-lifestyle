/* Read-only post-boot check of the exact 120-byte ulpt_match replacement. */
#include <sys/types.h>
#include <sys/utsname.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern int airport_pl2303_match(void *, void *, void *);

int main(void)
{
    struct utsname u;
    unsigned char actual[120], expected[120];
    int fd;
    ssize_t count;
    if (uname(&u) == -1 || strcmp(u.machine, "ar7240") ||
        !strstr(u.version, "AirPortFW-76200.16")) {
        fprintf(stderr, "Unexpected firmware; stop\n");
        return 1;
    }
    memset(expected, 0, sizeof(expected));
    memcpy(expected, (const void *)airport_pl2303_match, 88);
    fd = open("/dev/kmem", O_RDONLY);
    if (fd < 0) { perror("open kmem read-only"); return 1; }
    count = pread(fd, actual, sizeof(actual), (off_t)0x802423f4UL);
    close(fd);
    if (count != sizeof(actual)) {
        fprintf(stderr, "Kernel read failed: %ld bytes, %s\n", (long)count, strerror(errno));
        return 1;
    }
    if (memcmp(actual, expected, sizeof(actual))) {
        fprintf(stderr, "Running kernel does not match the reviewed PL2303 patch\n");
        return 1;
    }
    puts("PASS: running A1392 7.6.2 kernel contains all 120 expected ulpt_match bytes");
    puts("Kernel inspected read-only; no kernel or USB writes performed");
    return 0;
}
