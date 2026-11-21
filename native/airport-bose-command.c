/* Native one-shot controller for the patched A1392 + proven PL2303/Bose cable.
 * Enters TAP, selects Zone 1, sends a remote command, then escapes to Idle.
 * Synchronizes to idle with Bose first and waits for each acknowledgement.
 * Volume controls send five acknowledged steps; other controls send one.
 * Explicit allowlisted command only; no command at startup and no retries.
 * Command ACK means accepted by the command interface, not measured power.
 */
#include <sys/types.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern int airport_pl2303_open_bulk(void);
#ifndef AIRPORT_SERIAL_LOCK
#define AIRPORT_SERIAL_LOCK "/mnt/Memory/bose-serial.lock"
#endif
/* This CD20 ACKed EEE with no audible decrease. EE@ was user-confirmed
 * quieter; retain '@' as the volume command filler in both directions. */
static const struct { const char *name, *code; } commands[] = {
    {"power_toggle", "@@"}, {"volume_up", "A@"}, {"volume_down", "E@"},
    {"mute_toggle", "NN"}, {"aux", "II"}, {"cd", "JJ"}, {"fm", "KK"},
    {"tape", "LL"}, {"video1", "GG"}, {"video2", "HH"},
    {"play_pause", "CC"}, {"stop", "MM"}, {"previous", "BA"}, {"next", "DD"}
};
static unsigned char reply[256];
static size_t received;
static int sent;
static int diagnostic, legacy, hold, stay_tap;
static int idle_exit=1;
static struct timeval trace_start;

static void trace_bytes(const char *direction, const unsigned char *bytes, size_t n)
{
    struct timeval now;
    long ms;
    size_t i;
    if(!diagnostic)return;
    gettimeofday(&now,NULL);
    ms=(now.tv_sec-trace_start.tv_sec)*1000+(now.tv_usec-trace_start.tv_usec)/1000;
    fprintf(stderr,"TRACE %ld ms %s",ms,direction);
    for(i=0;i<n;++i)fprintf(stderr," %02x",bytes[i]);
    fputc('\n',stderr);
}

static void expired(int sig)
{
    static const char msg[] = "Command deadline reached; result unknown; no retry\n";
    (void)sig;
    (void)write(STDERR_FILENO, msg, sizeof(msg)-1);
    _exit(124);
}

static void pause_ms(long milliseconds)
{
    struct timespec delay;
    delay.tv_sec = milliseconds / 1000;
    delay.tv_nsec = (milliseconds % 1000) * 1000000;
    while (nanosleep(&delay, &delay) == -1 && errno == EINTR) {}
}

static int send_bytes(int fd, const char *bytes, size_t size)
{
    size_t i;
    for(i=0;i<size;++i) {
        if(write(fd,bytes+i,1)!=1)return -1;
        trace_bytes("TX",(const unsigned char *)bytes+i,1);
        ++sent;pause_ms(legacy?50:60);
    }
    return 0;
}

/* Read from the reader pipe: select() on ulpt itself is not supported.
 * Consume every byte in each chunk so echoes cannot become future ACKs. */
static int wait_for(int fd, unsigned char expected)
{
    struct timeval now, end, remaining;
    fd_set readable;
    unsigned char bytes[64];
    ssize_t n,i;
    int matched;
    gettimeofday(&end,NULL);end.tv_sec+=2;
    for(;;) {
        gettimeofday(&now,NULL);
        remaining.tv_sec=end.tv_sec-now.tv_sec;
        remaining.tv_usec=end.tv_usec-now.tv_usec;
        if(remaining.tv_usec<0){--remaining.tv_sec;remaining.tv_usec+=1000000;}
        if(remaining.tv_sec<0)return 0;
        FD_ZERO(&readable);FD_SET(fd,&readable);
        n=select(fd+1,&readable,NULL,NULL,&remaining);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)return 0;
        n=read(fd,bytes,sizeof(bytes));
        if(n<=0)return 0;
        matched=0;
        for(i=0;i<n;++i) {
            if(received<sizeof(reply))reply[received++]=bytes[i];
            if(bytes[i]=='W'&&expected=='X')matched=-1;
            else if(bytes[i]==expected&&matched>=0)matched=1;
        }
        if(matched)return matched;
    }
}

int main(int argc, char **argv)
{
    const char *code = NULL;
    char frame[21];
    unsigned char bytes[64];
    unsigned i, step, steps=1, steps_acknowledged=0, zone=1;
    int bulk, channel[2], status, saved_stdout, lock_fd, dry_run = 0;
    int acknowledged=0, attempted=0, response=0, zone_acknowledged=0, exit_sent=0;
    const char *stage="sync_idle";
    ssize_t count;
    pid_t reader;
    struct sigaction sa;
    if(argc==2 && !strcmp(argv[1],"--volume-down-test")) {
        diagnostic=1;steps=5;argv[1]="volume_down";
    }
    if(argc==2 && !strcmp(argv[1],"--volume-down-zone2-test")) {
        diagnostic=1;steps=5;zone=2;argv[1]="volume_down";
    }
    if(argc==2 && !strcmp(argv[1],"--volume-up-test")) {
        diagnostic=1;steps=5;argv[1]="volume_up";
    }
    if(argc==3 && (!strcmp(argv[1],"--diagnose") || !strcmp(argv[1],"--legacy") || !strcmp(argv[1],"--hold") || !strcmp(argv[1],"--stay-tap") || !strcmp(argv[1],"--idle-exit") || !strcmp(argv[1],"--tap-exit"))) {
        diagnostic=1;legacy=!strcmp(argv[1],"--legacy");hold=!strcmp(argv[1],"--hold");
        stay_tap=!strcmp(argv[1],"--stay-tap");
        idle_exit=strcmp(argv[1],"--tap-exit") && !hold;
        --argc;++argv;
        if(strcmp(argv[1],"aux")&&strcmp(argv[1],"fm")) {
            fprintf(stderr,"Diagnostic modes allow only aux or fm\n");return 2;
        }
    }
    if (argc == 3 && !strcmp(argv[1], "--dry-run")) {
        dry_run = 1;
        --argc;
        ++argv;
    }
    if (argc == 2)
        for (i = 0; i < sizeof(commands)/sizeof(commands[0]); ++i)
            if (!strcmp(argv[1], commands[i].name)) code = commands[i].code;
    if (!code) {
        fprintf(stderr, "usage: %s <command>\nCommands:", argv[0]);
        for (i = 0; i < sizeof(commands)/sizeof(commands[0]); ++i)
            fprintf(stderr, " %s", commands[i].name);
        fputc('\n', stderr);
        return 2;
    }
    if(!strcmp(argv[1],"volume_up") || !strcmp(argv[1],"volume_down"))steps=5;
    /* All commands have two data bytes (§5.3); zone's first is binary (§5.4).
     * '@' is the unused second data byte, not an ASCII digit for the zone. */
    snprintf(frame, sizeof(frame), "BoseTAP\raA\x01@aE%s%s", code, idle_exit?"Bose":"axxx");
    frame[10]=(char)zone;
    if(legacy)snprintf(frame,sizeof(frame),"TAP\raE%saxxx",code);
    if (dry_run) {
        for (i = 0; i < 12; ++i) printf("%02x", (unsigned char)frame[i]);
        for(step=0;step<steps;++step)
            for(i=12;i<16;++i)printf("%02x",(unsigned char)frame[i]);
        for (i = 16; i < 20; ++i) printf("%02x", (unsigned char)frame[i]);
        putchar('\n');
        return 0;
    }
    lock_fd = open(AIRPORT_SERIAL_LOCK, O_CREAT | O_RDWR, 0600);
    if (lock_fd == -1 || flock(lock_fd, LOCK_EX | LOCK_NB) == -1) {
        fprintf(stderr, "Serial controller busy or lock unavailable; no command sent\n");
        return 1;
    }
    /* Adapter setup logs go to stderr; stdout is the machine-readable result. */
    saved_stdout = dup(STDOUT_FILENO);
    if (fflush(stdout) || saved_stdout == -1 ||
        dup2(STDERR_FILENO, STDOUT_FILENO) == -1) { perror("output setup"); return 1; }
    setvbuf(stdout, NULL, _IONBF, 0);
    gettimeofday(&trace_start,NULL);
    bulk = airport_pl2303_open_bulk();
    if (dup2(saved_stdout, STDOUT_FILENO) == -1) { perror("restore output"); return 1; }
    close(saved_stdout);
    if (bulk < 0) return 1;
    if (pipe(channel) == -1) { perror("pipe"); close(bulk); return 1; }
    reader = fork();
    if (reader < 0) { perror("fork"); close(bulk); close(channel[0]); close(channel[1]); return 1; }
    if (reader == 0) {
        close(channel[0]);
        alarm(hold?35:9);
        /* One reader, one writer. Independent ulpt IN/OUT transfer handles. */
        for(;;) {
            count=read(bulk,bytes,sizeof(bytes));
            if(count<0 && (errno==ETIMEDOUT||errno==EINTR))continue;
            if(count<=0)break;
            trace_bytes("RX",bytes,(size_t)count);
            if (write(channel[1], bytes, (size_t)count) != count) break;
        }
        close(bulk);
        close(channel[1]);
        _exit(0);
    }
    close(channel[1]);
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = expired;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGALRM, &sa, NULL) == -1) {
        perror("alarm handler"); kill(reader, SIGTERM); waitpid(reader, NULL, 0);
        close(channel[0]); close(bulk); return 1;
    }
    alarm(hold?34:8);
    if(legacy) {
        stage="legacy_paced_frame";attempted=1;
        (void)send_bytes(bulk,frame,12);
        pause_ms(1000);
        goto done;
    }
    if(send_bytes(bulk,"Bose",4)<0)goto done;
    /* Returning to idle emits a banner on this unit. It is half duplex:
     * allow the banner to finish before transmitting TAP (otherwise its
     * first byte collides). Preserve, then drain that earlier response. */
    pause_ms(300);
    if(fcntl(channel[0],F_SETFL,O_NONBLOCK)<0)goto done;
    while((count=read(channel[0],bytes,sizeof(bytes)))>0)
        for(i=0;i<(unsigned)count;++i)if(received<sizeof(reply))reply[received++]=bytes[i];
    if(fcntl(channel[0],F_SETFL,0)<0)goto done;
    stage="enter_tap";
    if(send_bytes(bulk,"TAP\r",4)<0 || wait_for(channel[0],'\r')!=1)goto done;
    stage="zone_preamble";
    if(send_bytes(bulk,"a",1)<0 || wait_for(channel[0],'X')!=1)goto done;
    stage="zone_ack";
    if(send_bytes(bulk,frame+9,3)<0 || wait_for(channel[0],'X')!=1)goto done;
    zone_acknowledged=1;
    for(step=0;step<steps;++step) {
        stage="command_preamble";
        if(send_bytes(bulk,"a",1)<0 || wait_for(channel[0],'X')!=1)goto done;
        stage="command_ack";attempted=1;
        if(send_bytes(bulk,frame+13,3)<0)goto done;
        response=wait_for(channel[0],'X');
        if(response!=1)break;
        ++steps_acknowledged;
    }
    acknowledged=steps_acknowledged==steps;
    if(stay_tap && acknowledged) {
        stage="tap_left_active_for_diagnosis";
        pause_ms(500);
        goto done;
    }
    if(hold && acknowledged) {
        puts("PHASE: SOURCE ACKNOWLEDGED; HOLDING TAP OPEN FOR 10 SECONDS");
        trace_bytes("HOLD_BEFORE_EXIT",(const unsigned char *)"",0);
        pause_ms(10000);
    }
    stage="exit_tap";
    if(idle_exit) {
        /* CD20 serial specification §4.1.1: return from any mode to Idle. */
        stage="idle_escape";
        if(send_bytes(bulk,"Bose",4)==0){pause_ms(300);exit_sent=1;}
    } else {
        if(send_bytes(bulk,"a",1)==0 && wait_for(channel[0],'X')==1 && send_bytes(bulk,"xxx",3)==0)
            exit_sent=1;
    }
    if(hold && acknowledged) {
        puts(exit_sent?"PHASE: TAP EXIT SENT; KEEPING USB OPEN FOR 10 SECONDS":
             "PHASE: TAP EXIT FAILED; KEEPING USB OPEN FOR 10 SECONDS");
        trace_bytes("HOLD_AFTER_EXIT",(const unsigned char *)"",0);
        pause_ms(10000);
    }
done:
    kill(reader, SIGTERM);
    while (waitpid(reader, &status, 0) == -1 && errno == EINTR) {}
    while((count=read(channel[0],bytes,sizeof(bytes)))>0)
        for(i=0;i<(unsigned)count;++i)if(received<sizeof(reply))reply[received++]=bytes[i];
    alarm(0);
    close(channel[0]);
    if(hold)puts("PHASE: CLOSING USB NOW");
    close(bulk);
    printf("{\"command\":\"%s\",\"result\":\"%s\",\"stage\":\"%s\",\"zone\":%s,\"zone_acknowledged\":%s,\"bytes_submitted\":%d,\"bose_acknowledged\":%s,\"rx_hex\":\"",
           argv[1], acknowledged?"command_acknowledged":response<0?"command_rejected":
           attempted?"sent_unconfirmed":"handshake_failed_not_sent", stage,legacy?"null":zone==2?"2":"1",zone_acknowledged?"true":"false",sent,
           acknowledged?"true":response<0?"false":"null");
    for (i = 0; i < received; ++i) printf("%02x", reply[i]);
    printf("\",\"steps_requested\":%u,\"steps_acknowledged\":%u}\n",steps,steps_acknowledged);
    return acknowledged ? 0 : 1;
}
