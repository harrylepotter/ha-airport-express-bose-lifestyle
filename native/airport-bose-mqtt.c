/* Native MQTT bridge: outbound trusted-LAN connection, no listening socket.
 * MQTT-C handles MQTT 3.1.1 framing. Clean sessions, QoS 0 command subscription,
 * retained/duplicate rejection and no serial retries prevent stale toggles.
 * Credentials live in a mode-600 file, never arguments or logs.
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <sys/ioctl.h>
#include <dev/usb/usb.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <mqtt.h>
#include "airport-bose-state.h"

#ifndef ROOT
#define ROOT "bose/airport"
#endif
#ifndef ID
#define ID "bose_airport"
#endif
static const struct { const char *command, *name, *icon; } buttons[] = {
    {"power_toggle","Power toggle","mdi:power"},
    {"volume_up","Volume up","mdi:volume-plus"},
    {"volume_down","Volume down","mdi:volume-minus"},
    {"mute_toggle","Mute toggle","mdi:volume-mute"},
    {"aux","AUX","mdi:audio-input-rca"}, {"cd","CD","mdi:disc"},
    {"fm","Radio","mdi:radio"}, {"tape","Tape","mdi:cassette"},
    {"video1","Video 1","mdi:video-input-component"},
    {"video2","Video 2","mdi:video-input-component"},
    {"play_pause","Play / pause","mdi:play-pause"}, {"stop","Stop","mdi:stop"},
    {"previous","Previous","mdi:skip-previous"}, {"next","Next","mdi:skip-next"}
};
static struct mqtt_client client;
static unsigned char sendbuf[16384], recvbuf[4096];
static char server[64], username[256], password[256];
static int port = 1883, announce, ready, queue[8], queued;
static pid_t worker;
static int worker_fd = -1, worker_index;
static char worker_output[2048];
static size_t worker_size;
static time_t worker_start;
static volatile sig_atomic_t stopping;
static struct bose_state state;

ssize_t mqtt_pal_sendall(int fd, const void *buf, size_t len, int flags)
{
    ssize_t n = send(fd, buf, len, flags);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    return n < 0 ? MQTT_ERROR_SOCKET_ERROR : n;
}
ssize_t mqtt_pal_recvall(int fd, void *buf, size_t len, int flags)
{
    ssize_t n = recv(fd, buf, len, flags);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    return n <= 0 ? MQTT_ERROR_SOCKET_ERROR : n;
}
static void stop(int sig) { (void)sig; stopping = 1; }
static void tick(void) { struct timespec t = {0,100000000}; nanosleep(&t,NULL); }
static void publish(const char *topic, const char *value, int retained)
{
    mqtt_publish(&client, topic, value, strlen(value), retained ? MQTT_PUBLISH_RETAIN : 0);
}
static void publish_state(void)
{
    char payload[512];
    snprintf(payload,sizeof(payload),
        "{\"power\":\"%s\",\"source\":\"%s\",\"estimated\":true,"
        "\"feedback_supported\":false,\"reason\":\"%s\",\"updated_at\":%ld}",
        state.power<0?"unknown":state.power?"on":"off",state.source,state.reason,(long)state.updated);
    publish(ROOT "/state",payload,1);
}
static int topic_is(struct mqtt_response_publish *p, const char *topic)
{
    return p->topic_name_size == strlen(topic) && !memcmp(p->topic_name,topic,p->topic_name_size);
}
static void received(void **unused, struct mqtt_response_publish *p)
{
    unsigned i;
    (void)unused;
    if (topic_is(p,"homeassistant/status")) {
        if (p->application_message_size == 6 && !memcmp(p->application_message,"online",6)) announce = 1;
        return;
    }
    if (!topic_is(p,ROOT "/command")) return;
    if (p->retain_flag || p->dup_flag || !ready) {
        publish(ROOT "/result","{\"result\":\"ignored_retained_duplicate_or_unavailable\"}",0);
        return;
    }
    for (i=0;i<sizeof(buttons)/sizeof(buttons[0]);++i) {
        if (p->application_message_size == strlen(buttons[i].command) &&
            !memcmp(p->application_message,buttons[i].command,p->application_message_size)) {
            if (queued == 8) publish(ROOT "/result","{\"result\":\"busy_not_sent\"}",0);
            else queue[queued++] = (int)i;
            return;
        }
    }
    publish(ROOT "/result","{\"result\":\"invalid_command_not_sent\"}",0);
}
static void discovery(void)
{
    char topic[256], payload[1200];
    unsigned i;
    for(i=0;i<sizeof(buttons)/sizeof(buttons[0]);++i) {
        snprintf(topic,sizeof(topic),"homeassistant/button/" ID "/%s/config",buttons[i].command);
        snprintf(payload,sizeof(payload),
            "{\"name\":\"%s\",\"unique_id\":\"" ID "_%s\",\"command_topic\":\"" ROOT "/command\","
            "\"payload_press\":\"%s\",\"availability_topic\":\"" ROOT "/availability\","
            "\"qos\":0,\"retain\":false,\"icon\":\"%s\","
            "\"device\":{\"identifiers\":[\"" ID "\"],\"name\":\"Bose Lifestyle 25\","
            "\"manufacturer\":\"Bose\",\"model\":\"Lifestyle 25 via AirPort Express\",\"sw_version\":\"0.1.0\"}}",
            buttons[i].name,buttons[i].command,buttons[i].command,buttons[i].icon);
        publish(topic,payload,1);
    }
    publish("homeassistant/sensor/" ID "/last_result/config",
        "{\"name\":\"Last command result\",\"unique_id\":\"" ID "_last_result\","
        "\"state_topic\":\"" ROOT "/result\",\"value_template\":\"{{ value_json.result }}\","
        "\"json_attributes_topic\":\"" ROOT "/result\",\"entity_category\":\"diagnostic\","
        "\"availability_topic\":\"" ROOT "/availability\","
        "\"device\":{\"identifiers\":[\"" ID "\"]}}",1);
    publish("homeassistant/sensor/" ID "/estimated_power/config",
        "{\"name\":\"Estimated power\",\"unique_id\":\"" ID "_estimated_power\","
        "\"state_topic\":\"" ROOT "/state\",\"value_template\":\"{{ value_json.power }}\","
        "\"json_attributes_topic\":\"" ROOT "/state\",\"icon\":\"mdi:power\","
        "\"availability_topic\":\"" ROOT "/availability\","
        "\"device\":{\"identifiers\":[\"" ID "\"]}}",1);
    publish_state();
    announce = 0;
}
static int adapter_present(void)
{
    struct usb_device_info di;
    int fd, addr, i, found = 0;
    fd=open("/dev/usb0",O_RDONLY);
    if(fd<0) return 0;
    for(addr=1;addr<USB_MAX_DEVICES;++addr) {
        memset(&di,0,sizeof(di));di.udi_addr=addr;
        if(ioctl(fd,USB_DEVICEINFO,&di)==-1)continue;
        if(di.udi_vendorNo!=0x067b || di.udi_productNo!=0x2303)continue;
        for(i=0;i<USB_MAX_DEVNAMES;++i)
            if(!strncmp(di.udi_devnames[i],"ulpt0",sizeof(di.udi_devnames[i])))found=1;
    }
    close(fd);return found;
}
static int load_config(const char *path)
{
    FILE *f; struct stat st; char line[768], *value, *end; long number;
    f=fopen(path,"r");
    if(!f)return -1;
    if(fstat(fileno(f),&st)||!S_ISREG(st.st_mode)||(st.st_mode&077)||st.st_uid!=getuid()) { fclose(f);return -1; }
    while(fgets(line,sizeof(line),f)) {
        if(!strchr(line,'\n')&&!feof(f)){fclose(f);return -1;}
        line[strcspn(line,"\r\n")]=0;
        if(!line[0]||line[0]=='#')continue;
        value=strchr(line,'=');if(!value){fclose(f);return -1;}*value++=0;
        if(!strcmp(line,"server")&&strlen(value)<sizeof(server))strcpy(server,value);
        else if(!strcmp(line,"username")&&strlen(value)<sizeof(username))strcpy(username,value);
        else if(!strcmp(line,"password")&&strlen(value)<sizeof(password))strcpy(password,value);
        else if(!strcmp(line,"port")) {
            errno=0;number=strtol(value,&end,10);
            if(errno||*end||number<1||number>65535){fclose(f);return -1;}port=(int)number;
        } else {fclose(f);return -1;}
    }
    fclose(f);return !server[0]||!username[0]||!password[0] ? -1 : 0;
}
static int connect_broker(void)
{
    struct sockaddr_in peer; struct timeval timeout={5,0}; fd_set fds;
    socklen_t size; int fd,error=0; uint32_t ip;
    memset(&peer,0,sizeof(peer));peer.sin_family=AF_INET;peer.sin_port=htons(port);
    if(inet_pton(AF_INET,server,&peer.sin_addr)!=1)return -1;
    ip=ntohl(peer.sin_addr.s_addr);
    if((ip>>24)!=10&&(ip>>20)!=0xac1&&(ip>>16)!=0xc0a8)return -1;
    fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0)return -1;
    fcntl(fd,F_SETFD,FD_CLOEXEC);fcntl(fd,F_SETFL,O_NONBLOCK);
    if(connect(fd,(struct sockaddr *)&peer,sizeof(peer))<0&&errno!=EINPROGRESS){close(fd);return -1;}
    FD_ZERO(&fds);FD_SET(fd,&fds);
    if(select(fd+1,NULL,&fds,NULL,&timeout)<=0){close(fd);return -1;}
    size=sizeof(error);
    if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size)<0||error){close(fd);return -1;}
    return fd;
}
static void start_worker(const char *executable)
{
    int fds[2],i;
    if(worker||!queued)return;
    worker_index=queue[0];for(i=1;i<queued;++i)queue[i-1]=queue[i];--queued;
    if(pipe(fds)<0){publish(ROOT "/result","{\"result\":\"spawn_failed_not_sent\"}",0);return;}
    worker=fork();
    if(worker==0) {
        close(fds[0]);dup2(fds[1],STDOUT_FILENO);close(fds[1]);
        execl(executable,executable,buttons[worker_index].command,(char *)NULL);
        _exit(127);
    }
    close(fds[1]);
    if(worker<0){close(fds[0]);worker=0;publish(ROOT "/result","{\"result\":\"spawn_failed_not_sent\"}",0);return;}
    worker_fd=fds[0];fcntl(worker_fd,F_SETFL,O_NONBLOCK);fcntl(worker_fd,F_SETFD,FD_CLOEXEC);
    worker_size=0;worker_start=time(NULL);
}
static void poll_worker(int connected)
{
    ssize_t n;int status=0;pid_t finished;
    if(!worker)return;
    while(worker_size<sizeof(worker_output)-1 &&
          (n=read(worker_fd,worker_output+worker_size,sizeof(worker_output)-1-worker_size))>0)worker_size+=(size_t)n;
    finished=waitpid(worker,&status,WNOHANG);
    if(finished<0&&errno==EINTR)return;
    if(finished==0) {
        if(time(NULL)-worker_start>20)kill(worker,SIGKILL);
        return;
    }
    /* Drain bytes written between the first read and the child's exit. */
    while(worker_size<sizeof(worker_output)-1 &&
          (n=read(worker_fd,worker_output+worker_size,sizeof(worker_output)-1-worker_size))>0)worker_size+=(size_t)n;
    while(worker_size && (worker_output[worker_size-1]=='\n'||worker_output[worker_size-1]=='\r'))--worker_size;
    worker_output[worker_size]=0;
    if(connected) {
        if(finished>0 && worker_size>2 && worker_output[0]=='{' && worker_output[worker_size-1]=='}' &&
           worker_size<sizeof(worker_output)-1 && WIFEXITED(status)) {
            publish(ROOT "/result",worker_output,0);
            bose_state_command(&state,buttons[worker_index].command,
                WEXITSTATUS(status)==0 && strstr(worker_output,"\"bose_acknowledged\":true")!=NULL,time(NULL));
        } else {
            publish(ROOT "/result","{\"result\":\"controller_failed_result_unknown\",\"bose_acknowledged\":null}",0);
            bose_state_unknown(&state,"controller_error",time(NULL));
        }
        publish_state();
    } else {
        bose_state_unknown(&state,"connection_lost",time(NULL));
    }
    close(worker_fd);worker_fd=-1;worker=0;
}
int main(int argc,char **argv)
{
    int fd,lockfd,last_ready=-1,authenticated; time_t started,last_health; unsigned retries=0;
    if(argc!=3||argv[2][0]!='/'){fprintf(stderr,"usage: %s <private-config> <absolute-command-executable>\n",argv[0]);return 2;}
    if(load_config(argv[1])){fprintf(stderr,"Invalid/private MQTT configuration required\n");return 1;}
    lockfd=open("/mnt/Memory/bose-mqtt.lock",O_CREAT|O_RDWR,0600);
    if(lockfd<0||flock(lockfd,LOCK_EX|LOCK_NB)<0){fprintf(stderr,"MQTT bridge already running or lock unavailable\n");return 1;}
    fcntl(lockfd,F_SETFD,FD_CLOEXEC);
    signal(SIGPIPE,SIG_IGN);signal(SIGTERM,stop);signal(SIGINT,stop);
    setvbuf(stdout,NULL,_IONBF,0);
    while(!stopping) {
        fd=connect_broker();
        if(fd<0){if(!(retries++%12))fprintf(stderr,"MQTT broker unavailable; retrying connection only\n");
            for(started=time(NULL);!stopping&&time(NULL)-started<5;){poll_worker(0);tick();}continue;}
        mqtt_init(&client,fd,sendbuf,sizeof(sendbuf),recvbuf,sizeof(recvbuf),received);
        mqtt_connect(&client,ID,ROOT "/availability","offline",7,username,password,
                     MQTT_CONNECT_CLEAN_SESSION|MQTT_CONNECT_WILL_RETAIN|MQTT_CONNECT_WILL_QOS_1,30);
        mqtt_subscribe(&client,ROOT "/command",0);mqtt_subscribe(&client,"homeassistant/status",0);
        announce=1;ready=0;queued=0;last_ready=-1;last_health=0;started=time(NULL);
        authenticated=0;
        bose_state_unknown(&state,"startup_or_reconnect",time(NULL));
        fprintf(stderr,"MQTT connection established; authenticating\n");
        while(!stopping && mqtt_sync(&client)==MQTT_OK) {
            if(!authenticated) {
                if(mqtt_mq_find(&client.mq,MQTT_CONTROL_CONNECT,NULL)!=NULL) {
                    if(time(NULL)-started>=10)break;
                    tick();continue;
                }
                authenticated=1;
                fprintf(stderr,"MQTT authenticated; discovery and commands enabled\n");
                /* Boot fuse: remove only after a successful broker CONNACK.
                 * No serial command or USB adapter is needed for healthy boot. */
                (void)unlink("/mnt/Flash/bose.try");
            }
            if(announce)discovery();
            if(time(NULL)-last_health>=5) {
                ready=adapter_present();last_health=time(NULL);
                if(ready!=last_ready){
                    publish(ROOT "/availability",ready?"online":"offline",1);last_ready=ready;
                    if(!ready){bose_state_unknown(&state,"adapter_missing",time(NULL));publish_state();}
                }
            }
            /* CD can shut itself off after inactivity. Avoid leaving an
             * indefinitely stale power estimate even with no HA commands. */
            if(state.power>=0 && time(NULL)-state.updated>=1200) {
                bose_state_unknown(&state,"estimate_expired",time(NULL));publish_state();
            }
            poll_worker(1);
            if(ready)start_worker(argv[2]);else queued=0;
            tick();
        }
        queued=0;ready=0;
        if(stopping){publish(ROOT "/availability","offline",1);mqtt_sync(&client);mqtt_disconnect(&client);mqtt_sync(&client);}
        else fprintf(stderr,"MQTT disconnected (%s); queued commands discarded\n",mqtt_error_str(client.error));
        close(fd);
        for(started=time(NULL);!stopping&&time(NULL)-started<5;){poll_worker(0);tick();}
    }
    /* Let an already-issued command finish; never interrupt/replay a toggle. */
    while(worker){poll_worker(0);tick();}
    return 0;
}
