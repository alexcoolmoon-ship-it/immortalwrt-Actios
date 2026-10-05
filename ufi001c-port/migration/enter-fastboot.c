/* SPDX-License-Identifier: GPL-2.0-only */
/* UFI001C migration helper. Default: check only. */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/reboot.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <unistd.h>

static int check_board(void)
{
    unsigned char data[4096];
    const char expected[] = "thwc,ufi001c";
    FILE *f = fopen("/proc/device-tree/compatible", "rb");
    if (!f) { perror("Read device-tree compatible"); return 0; }
    size_t len = fread(data, 1, sizeof(data), f);
    int failed = ferror(f);
    fclose(f);
    if (failed) return 0;
    for (size_t at = 0; at < len;) {
        size_t n = strnlen((const char *)data + at, len - at);
        if (n == sizeof(expected) - 1 && at + n < len &&
            memcmp(data + at, expected, sizeof(expected)) == 0) return 1;
        at += n + 1;
    }
    fprintf(stderr, "Refusing: this is not a thwc,ufi001c board.\n");
    return 0;
}

int main(int argc, char **argv)
{
    int do_reboot = argc == 2 && strcmp(argv[1], "--reboot") == 0;
    if (argc > 2 || (argc == 2 && !do_reboot && strcmp(argv[1], "--check"))) {
        fprintf(stderr, "Usage: %s [--check | --reboot]\n", argv[0]);
        return 2;
    }
    if (!check_board()) return 1;
    if (!do_reboot) { puts("UFI001C detected. Check only; no changes made."); return 0; }
    if (geteuid() != 0) { fprintf(stderr, "Root privileges required.\n"); return 1; }
    struct statvfs fs;
    if (statvfs("/", &fs) != 0) { perror("statvfs"); return 1; }
    int was_readonly = !!(fs.f_flag & ST_RDONLY);
    sync();
    if (!was_readonly && mount(NULL, "/", NULL, MS_REMOUNT | MS_RDONLY, NULL)) {
        perror("Cannot remount root read-only; reboot cancelled");
        return 1;
    }
    puts("Rebooting UFI001C into bootloader mode.");
    fflush(stdout);
    sync();
    syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
            LINUX_REBOOT_CMD_RESTART2, "bootloader");
    int saved_errno = errno;
    if (!was_readonly && mount(NULL, "/", NULL, MS_REMOUNT, NULL))
        perror("Could not restore read-write root filesystem");
    errno = saved_errno;
    perror("Reboot failed");
    return 1;
}
