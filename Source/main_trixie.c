#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>

static pid_t python_pid = 0;
static time_t last_button_time = 0;

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
}

int main() {
    char buf0[2], buf2[2], buf3[2];
    int fd0, fd2, fd3;
    int last_k1 = 0, last_k2 = 0, last_k3 = 0;
    int curr_k1, curr_k2, curr_k3;
    time_t now;
    
    if (fork() != 0) exit(0);
    
    FILE *fp = fopen("/var/run/nanohat-oled.pid", "w");
    if (fp) {
        fprintf(fp, "%d\n", getpid());
        fclose(fp);
    }
    
    export_gpio(0);
    export_gpio(2);
    export_gpio(3);
    
    fd0 = open("/sys/class/gpio/gpio0/value", O_RDONLY);
    fd2 = open("/sys/class/gpio/gpio2/value", O_RDONLY);
    fd3 = open("/sys/class/gpio/gpio3/value", O_RDONLY);
    
    if(fd0 < 0 || fd2 < 0 || fd3 < 0) {
        return 1;
    }
    
    start_python_script();
    sleep(2);
    
    while (1) {
        lseek(fd0, 0, SEEK_SET);
        read(fd0, buf0, 2);
        curr_k1 = (buf0[0] == '1') ? 1 : 0;
        
        lseek(fd2, 0, SEEK_SET);
        read(fd2, buf2, 2);
        curr_k2 = (buf2[0] == '1') ? 1 : 0;
        
        lseek(fd3, 0, SEEK_SET);
        read(fd3, buf3, 2);
        curr_k3 = (buf3[0] == '1') ? 1 : 0;
        
        time(&now);
        
        if (now - last_button_time >= 1) {
            if (curr_k1 == 1 && last_k1 == 0) {
                if (python_pid > 0) kill(python_pid, SIGUSR1);
                last_button_time = now;
            }
            if (curr_k2 == 1 && last_k2 == 0) {
                if (python_pid > 0) kill(python_pid, SIGUSR2);
                last_button_time = now;
            }
            if (curr_k3 == 1 && last_k3 == 0) {
                if (python_pid > 0) kill(python_pid, SIGALRM);
                last_button_time = now;
            }
        }
        
        last_k1 = curr_k1;
        last_k2 = curr_k2;
        last_k3 = curr_k3;
        
        if (waitpid(python_pid, NULL, WNOHANG) > 0) {
            sleep(2);
            start_python_script();
        }
        
        usleep(50000);
    }
}