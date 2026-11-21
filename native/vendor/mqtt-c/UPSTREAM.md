MQTT-C by Liam Bindle, MIT licence (see LICENSE).

Source: https://github.com/LiamBindle/MQTT-C
Revision: 102e59c14dd0e01770b357a462bd462c83de62e2

mqtt.h and mqtt_pal.h are unchanged. mqtt.c adds optional
MQTT_NO_RESPONSE_TIME_STATS to disable unused floating-point RTT diagnostics,
and removes a redundant float cast of the integer keepalive interval. Protocol
timeouts and retransmission behavior are unchanged. The application selects its
own single-threaded NetBSD platform adapter with MQTTC_PAL_FILE.
