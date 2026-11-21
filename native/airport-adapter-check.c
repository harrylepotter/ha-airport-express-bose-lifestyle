/* Read-only descriptors. No serial bytes, reset, vendor request or USB setup.
 * Exit status gates the installer; compatible means the examined descriptor
 * layout, not a guarantee of the adapter board's electrical voltage levels. */
#include <sys/types.h>
#include <sys/ioctl.h>
#include <dev/usb/usb.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static unsigned le16(const unsigned char *p) { return p[0] | (unsigned)p[1] << 8; }
static int descriptor(int fd,int addr,int type,void *data,unsigned size)
{
    struct usb_ctl_request r;
    memset(&r,0,sizeof(r));r.ucr_addr=addr;r.ucr_request.bmRequestType=0x80;
    r.ucr_request.bRequest=6;USETW(r.ucr_request.wValue,type<<8);
    USETW(r.ucr_request.wLength,size);r.ucr_data=data;
    return ioctl(fd,USB_REQUEST,&r)==0 && r.ucr_actlen==(int)size;
}
int main(int argc,char **argv)
{
    struct usb_device_info di;
    unsigned char d[18],c[256];
    int fd,addr,devices=0,compatible=0,absent;
    unsigned total,offset,n,in=0,out=0,intr=0,iface=0;
    if(argc!=2 || (strcmp(argv[1],"--compatible") && strcmp(argv[1],"--absent")))return 2;
    absent=!strcmp(argv[1],"--absent");
    fd=open("/dev/usb0",O_RDWR);if(fd<0){perror("usb0");return 1;}
    for(addr=1;addr<USB_MAX_DEVICES;++addr) {
        memset(&di,0,sizeof(di));di.udi_addr=addr;
        if(ioctl(fd,USB_DEVICEINFO,&di)<0)continue;
        if(di.udi_class==9)continue; /* host root hub or passive external hub */
        ++devices;
        if(di.udi_vendorNo!=0x067b || di.udi_productNo!=0x2303)continue;
        if(!descriptor(fd,addr,1,d,sizeof(d)))continue;
        if(d[0]!=18 || d[1]!=1 || le16(d+2)!=0x0110 || d[4]!=0 || d[7]!=64 ||
           le16(d+8)!=0x067b || le16(d+10)!=0x2303 || le16(d+12)!=0x0300 || d[17]!=1)continue;
        if(!descriptor(fd,addr,2,c,9) || c[0]!=9 || c[1]!=2)continue;
        total=le16(c+2);if(total<9 || total>sizeof(c))continue;
        if(!descriptor(fd,addr,2,c,total) || c[4]!=1 || c[5]!=1)continue;
        in=out=intr=iface=0;
        for(offset=9;offset+2<=total;offset+=n) {
            n=c[offset];if(n<2 || offset+n>total)break;
            if(c[offset+1]==4 && n>=9 && c[offset+2]==0 && c[offset+3]==0 && c[offset+4]==3)++iface;
            if(c[offset+1]==5 && n>=7) {
                if(c[offset+2]==0x83 && (c[offset+3]&3)==2 && le16(c+offset+4)==64)++in;
                if(c[offset+2]==0x02 && (c[offset+3]&3)==2 && le16(c+offset+4)==64)++out;
                if(c[offset+2]==0x81 && (c[offset+3]&3)==3)++intr;
            }
        }
        if(offset==total && iface==1 && in==1 && out==1 && intr==1)++compatible;
    }
    close(fd);
    printf("{\"usb_devices\":%d,\"compatible_adapters\":%d}\n",devices,compatible);
    return absent ? (devices!=0) : (devices!=1 || compatible!=1);
}
