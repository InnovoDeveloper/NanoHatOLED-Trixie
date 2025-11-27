#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/select.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <errno.h>

static int gpio_fds[3];
static pid_t python_pid = 0;

int init_gpio(int gpio) {
    char path[64];
    FILE *fp;
    int fd;
    
    // Export GPIO
    fp = fopen("/sys/class/gpio/export", "w");
    if (fp) {
        fprintf(fp, "%d\n", gpio);
        fclose(fp);
    }
    usleep(100000);
    
    // Set direction
    sprintf(path, "/sys/class/gpio/gpio%d/direction", gpio);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "in\n");
        fclose(fp);
    }
    
    // Set edge detection
    sprintf(path, "/sys/class/gpio/gpio%d/edge", gpio);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "rising\n");
        fclose(fp);
    }
    
    // Open value file
    sprintf(path, "/sys/class/gpio/gpio%d/value", gpio);
    fd = open(path, O_RDONLY);
    if (fd < 0) {
        printf("Failed to open GPIO %d\n", gpio);
    }
    return fd;
}

void start_python_script() {
    python_pid = fork();
    if (python_pid == 0) {
        // Child process
        chdir("/root/NanoHatOLED/BakeBit/Software/Python");
        system("python3 bakebit_nanohat_oled.py > /tmp/oled.log 2>&1");
        exit(1);
    }
    printf("Started Python script with PID %d\n", python_pid);
}

int main() {
    fd_set rfds;
    char buf[2];
    int retval, maxfd = 0;
    
    // Daemonize
    if (fork() != 0) exit(0);
    
    // Write PID file
    FILE *pidfile = fopen("/var/run/nanohat-oled.pid", "w");
    if (pidfile) {
        fprintf(pidfile, "%d\n", getpid());
        fclose(pidfile);
    }
    
    printf("Initializing GPIOs...\n");
    gpio_fds[0] = init_gpio(0);  // K1
    gpio_fds[1] = init_gpio(2);  // K2
    gpio_fds[2] = init_gpio(3);  // K3
    
    for (int i = 0; i < 3; i++) {
        if (gpio_fds[i] > maxfd) maxfd = gpio_fds[i];
    }
    
    // Start Python script
    start_python_script();
    sleep(3);
    
    printf("Monitoring buttons...\n");
    while (1) {
        FD_ZERO(&rfds);
        for (int i = 0; i < 3; i++) {
            if (gpio_fds[i] >= 0) {
                FD_SET(gpio_fds[i], &rfds);
            }
        }
        
        struct timeval tv;
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        
        retval = select(maxfd + 1, &rfds, NULL, NULL, &tv);
        
        if (retval > 0) {
            for (int i = 0; i < 3; i++) {
                if (gpio_fds[i] >= 0 && FD_ISSET(gpio_fds[i], &rfds)) {
                    lseek(gpio_fds[i], 0, SEEK_SET);
                    read(gpio_fds[i], buf, 2);
                    
                    // Send signal to Python script
                    if (python_pid > 0) {
                        switch(i) {
                            case 0: kill(python_pid, SIGUSR1); break;  // K1
                            case 1: kill(python_pid, SIGUSR2); break;  // K2
                            case 2: kill(python_pid, SIGALRM); break;  // K3
                        }
                        printf("Button K%d pressed\n", i+1);
                    }
                }
            }
        }
        
        // Check if Python script is still running
        int status;
        if (waitpid(python_pid, &status, WNOHANG) > 0) {
            printf("Python script died, restarting...\n");
            sleep(2);
            start_python_script();
        }
    }
    
    return 0;
}
