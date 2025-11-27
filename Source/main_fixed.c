#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>

static int gpio_fds[3];
static pid_t python_pid = 0;
static time_t last_press[3] = {0, 0, 0};

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
    
    // Open value file for polling (no edge detection)
    sprintf(path, "/sys/class/gpio/gpio%d/value", gpio);
    fd = open(path, O_RDONLY);
    return fd;
}

void start_python_script() {
    python_pid = fork();
    if (python_pid == 0) {
        chdir("/root/NanoHatOLED/BakeBit/Software/Python");
        execlp("python3", "python3", "bakebit_nanohat_oled.py", NULL);
        exit(1);
    }
    printf("Started Python script with PID %d\n", python_pid);
}

int main() {
    char buf[2];
    int button_state[3] = {1, 1, 1};  // Previous state (1=not pressed)
    int current_state;
    time_t now;
    
    // Daemonize
    if (fork() != 0) exit(0);
    
    // Write PID file
    FILE *pidfile = fopen("/var/run/nanohat-oled.pid", "w");
    if (pidfile) {
        fprintf(pidfile, "%d\n", getpid());
        fclose(pidfile);
    }
    
    // Initialize GPIOs
    gpio_fds[0] = init_gpio(0);  // K1
    gpio_fds[1] = init_gpio(2);  // K2
    gpio_fds[2] = init_gpio(3);  // K3
    
    // Start Python script
    start_python_script();
    sleep(2);
    
    // Poll buttons
    while (1) {
        for (int i = 0; i < 3; i++) {
            if (gpio_fds[i] >= 0) {
                lseek(gpio_fds[i], 0, SEEK_SET);
                if (read(gpio_fds[i], buf, 2) > 0) {
                    current_state = (buf[0] == '0') ? 0 : 1;
                    
                    // Detect button press (transition from 1 to 0)
                    if (button_state[i] == 1 && current_state == 0) {
                        time(&now);
                        // Debounce - ignore if pressed within 0.5 seconds
                        if (now - last_press[i] > 0) {
                            last_press[i] = now;
                            if (python_pid > 0) {
                                switch(i) {
                                    case 0: 
                                        kill(python_pid, SIGUSR1); 
                                        printf("K1 pressed\n");
                                        break;
                                    case 1: 
                                        kill(python_pid, SIGUSR2);
                                        printf("K2 pressed\n");
                                        break;
                                    case 2: 
                                        kill(python_pid, SIGALRM);
                                        printf("K3 pressed\n");
                                        break;
                                }
                            }
                        }
                    }
                    button_state[i] = current_state;
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
        
        usleep(50000);  // 50ms polling interval
    }
    
    return 0;
}
