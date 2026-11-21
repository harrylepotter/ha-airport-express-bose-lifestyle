#!/bin/sh
# Called before ACPd starts its printer services. Root is a disposable RAM disk.
# These Apple programs are hardlinks: unlink directory entries, NEVER overwrite.
if [ -f /mnt/Flash/bose.enable -a -x /mnt/Flash/bose-printer-stub ]; then
    for bose_printer in printd mobilemed; do
        /bin/rm -f /sbin/$bose_printer &&
            /bin/ln -s /mnt/Flash/bose-printer-stub /sbin/$bose_printer
    done
fi
exit 0
