#ifndef AIRPORT_MQTT_PAL_H
#define AIRPORT_MQTT_PAL_H
/* Single-threaded NetBSD 4 port of MQTT-C's documented platform interface. */
#include <sys/types.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <stdarg.h>
/* NetBSD 4's header names an obsolete GCC builtin; o32 va_list ABI is unchanged. */
#undef va_start
#define va_start(ap,last) __builtin_va_start(ap,last)
#include <time.h>
#include <arpa/inet.h>
typedef int mqtt_pal_socket_handle;
typedef int mqtt_pal_mutex_t;
typedef time_t mqtt_pal_time_t;
#define MQTT_PAL_HTONS(x) htons(x)
#define MQTT_PAL_NTOHS(x) ntohs(x)
#define MQTT_PAL_TIME() time(NULL)
#define MQTT_PAL_MUTEX_INIT(p) ((void)(p))
#define MQTT_PAL_MUTEX_LOCK(p) ((void)(p))
#define MQTT_PAL_MUTEX_UNLOCK(p) ((void)(p))
ssize_t mqtt_pal_sendall(int, const void *, size_t, int);
ssize_t mqtt_pal_recvall(int, void *, size_t, int);
#endif
