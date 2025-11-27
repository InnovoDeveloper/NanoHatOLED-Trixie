#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>

int main() {
    char buf0[2], buf2[2], buf3[2];
    int fd0 = open("/sys/class/gpio/gpio0/value", O_RDONLY);
    int fd2 = open("/sys/class/gpio/gpio2/value", O_RDONLY);
    int fd3 = open("/sys/class/gpio/gpio3/value", O_RDONLY);
    
    printf("fd0=%d, fd2=%d, fd3=%d\n", fd0, fd2, fd3);
    
    if(fd0 < 0 || fd2 < 0 || fd3 < 0) {
        printf("Failed to open GPIO files\n");
        return 1;
    }
    
    printf("Reading button states...\n");
    
    while(1) {
        lseek(fd0, 0, SEEK_SET);
        read(fd0, buf0, 2);
        
        lseek(fd2, 0, SEEK_SET);
        read(fd2, buf2, 2);
        
        lseek(fd3, 0, SEEK_SET);
        read(fd3, buf3, 2);
        
        printf("\rK1=%c K2=%c K3=%c", buf0[0], buf2[0], buf3[0]);
        fflush(stdout);
        
        usleep(100000);
    }
}
