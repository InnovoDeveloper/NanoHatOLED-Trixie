#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>

static pid_t python_pid = 0;

void start_python_script() {
    python_pid = fork();
    if (python_pid == 0) {
        chdir("/root/NanoHatOLED/BakeBit/Software/Python");
        execlp("python3", "python3", "bakebit_nanohat_oled.py", NULL);
        exit(1);
    }
}

int main() {
    char buf[2];
    int fd0, fd2, fd3;
    int last_state[3] = {0, 0, 0};  // Inverted: 0 = not pressed
    int current;
    
    if (fork() != 0) exit(0);
    
    FILE *fp = fopen("/var/run/nanohat-oled.pid", "w");
    if (fp) {
        fprintf(fp, "%d\n", getpid());
        fclose(fp);
    }
    
    fd0 = open("/sys/class/gpio/gpio0/value", O_RDONLY);
    fd2 = open("/sys/class/gpio/gpio2/value", O_RDONLY);
    fd3 = open("/sys/class/gpio/gpio3/value", O_RDONLY);
    
    start_python_script();
    sleep(2);
    
    while (1) {
        // Check K1 - inverted logic
        lseek(fd0, 0, SEEK_SET);
        read(fd0, buf, 2);
        current = (buf[0] == '1') ? 1 : 0;  // 1 = pressed now
        if (current == 1 && last_state[0] == 0) {
            if (python_pid > 0) kill(python_pid, SIGUSR1);
        }
        last_state[0] = current;
        
        // Check K2
        lseek(fd2, 0, SEEK_SET);
        read(fd2, buf, 2);
        current = (buf[0] == '1') ? 1 : 0;
        if (current == 1 && last_state[1] == 0) {
            if (python_pid > 0) kill(python_pid, SIGUSR2);
        }
        last_state[1] = current;
        
        // Check K3
        lseek(fd3, 0, SEEK_SET);
        read(fd3, buf, 2);
        current = (buf[0] == '1') ? 1 : 0;
        if (current == 1 && last_state[2] == 0) {
            if (python_pid > 0) kill(python_pid, SIGALRM);
        }
        last_state[2] = current;
        
        if (waitpid(python_pid, NULL, WNOHANG) > 0) {
            sleep(2);
            start_python_script();
        }
        
        usleep(50000);
    }
}
