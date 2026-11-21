#!/bin/sh
# Run once in the background by the firmware rc hook. Network retries belong
# to the daemon; never restart/replay a Bose command from this script.
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
exec /mnt/Flash/bose-mqtt /mnt/Flash/bose-mqtt.conf /mnt/Flash/bose-command
