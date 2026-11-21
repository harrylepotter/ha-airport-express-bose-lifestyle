/* Command-derived estimates, never a claim of measured Bose state. */
#ifndef AIRPORT_BOSE_STATE_H
#define AIRPORT_BOSE_STATE_H
#include <string.h>
#include <time.h>
struct bose_state {
    int power; /* -1 unknown, 0 off, 1 on */
    const char *source;
    const char *reason;
    time_t updated;
};
static void bose_state_unknown(struct bose_state *s, const char *reason, time_t now)
{
    s->power=-1;s->source="unknown";s->reason=reason;s->updated=now;
}
static void bose_state_command(struct bose_state *s,const char *command,int acknowledged,time_t now)
{
    if(!acknowledged){bose_state_unknown(s,"command_unconfirmed",now);return;}
    if(!strcmp(command,"aux")||!strcmp(command,"cd")||!strcmp(command,"fm")||
       !strcmp(command,"tape")||!strcmp(command,"video1")||!strcmp(command,"video2")) {
        s->power=1;
        /* The AM/FM command can toggle bands; do not claim FM specifically. */
        s->source=!strcmp(command,"fm")?"radio":command;
    } else if(!strcmp(command,"power_toggle")) {
        if(s->power>=0)s->power=!s->power;
        /* Power-on can restore a source changed outside this bridge. */
        s->source="unknown";
    }
    s->reason="acknowledged_command_estimate";s->updated=now;
}
#endif
