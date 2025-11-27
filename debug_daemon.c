#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>

static pid_t python_pid = 0;

void export_gpio(int gpio) {
    FILE *fp = fopen("/sys/class/gpio/export", "w");
    if (fp) {
        fprintf(fp, "%d\n", gpio);
        fclose(fp);
    }
    usleep(100000);
    
    char path[64];
    sprintf(path, "/sys/class/gpio/gpio%d/direction", gpio);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "in\n");
        fclose(fp);
    }
}

void start_python_script() {
    python_pid = fork();
    if (python_pid == 0) {
        chdir("/root/NanoHatOLED/BakeBit/Software/Python");
        execlp("python3", "python3", "bakebit_nanohat_oled.py", NULL);
        exit(1);
    }
    printf("Started Python script PID %d\n", python_pid);
}

int main() {
    char buf0[2], buf2[2], buf3[2];
    int fd0, fd2, fd3;
    int last_k1 = 0, last_k2 = 0, last_k3 = 0;
    
    // DON'T fork to background so we can see debug output
    
    export_gpio(0);
    export_gpio(2);
    export_gpio(3);
    
    fd0 = open("/sys/class/gpio/gpio0/value", O_RDONLY);
    fd2 = open("/sys/class/gpio/gpio2/value", O_RDONLY);
    fd3 = open("/sys/class/gpio/gpio3/value", O_RDONLY);
    
    printf("GPIO fds: %d %d %d\n", fd0, fd2, fd3);
    
    start_python_script();
    sleep(2);
    
    printf("Monitoring buttons...\n");
    int loop = 0;
    
    while (1) {
        lseek(fd0, 0, SEEK_SET);
        read(fd0, buf0, 2);
        lseek(fd2, 0, SEEK_SET);
        read(fd2, buf2, 2);
        lseek(fd3, 0, SEEK_SET);
        read(fd3, buf3, 2);
        
        // Print state every 20 loops (1 second)
        if (++loop % 20 == 0) {
            printf("Button states: K1=%c K2=%c K3=%c\n", buf0[0], buf2[0], buf3[0]);
        }
        
        // Check for button press (0->1 transition)
        if (buf0[0] == '1' && last_k1 == 0) {
            printf("K1 PRESSED! Sending SIGUSR1 to PID %d\n", python_pid);
            kill(python_pid, SIGUSR1);
        }
        if (buf2[0] == '1' && last_k2 == 0) {
            printf("K2 PRESSED! Sending SIGUSR2 to PID %d\n", python_pid);
            kill(python_pid, SIGUSR2);
        }
        if (buf3[0] == '1' && last_k3 == 0) {
            printf("K3 PRESSED! Sending SIGALRM to PID %d\n", python_pid);
            kill(python_pid, SIGALRM);
        }
        
        last_k1 = (buf0[0] == '1') ? 1 : 0;
        last_k2 = (buf2[0] == '1') ? 1 : 0;
        last_k3 = (buf3[0] == '1') ? 1 : 0;
        
        usleep(50000);
    }
}
