#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>

int main() {
    char buf[2];
    int fd0 = open("/sys/class/gpio/gpio0/value", O_RDONLY);
    int fd2 = open("/sys/class/gpio/gpio2/value", O_RDONLY);
    int fd3 = open("/sys/class/gpio/gpio3/value", O_RDONLY);
    
    printf("Testing button reading...\n");
    printf("Press buttons to test\n");
    
    while(1) {
        lseek(fd0, 0, SEEK_SET);
        read(fd0, buf, 2);
        if(buf[0] == '0') printf("K1 pressed\n");
        
        lseek(fd2, 0, SEEK_SET);
        read(fd2, buf, 2);
        if(buf[0] == '0') printf("K2 pressed\n");
        
        lseek(fd3, 0, SEEK_SET);
        read(fd3, buf, 2);
        if(buf[0] == '0') printf("K3 pressed\n");
        
        usleep(100000);
    }
}
