#include <stdint.h>
#include "bio.h" // must always be first
#define PIN_SCL   16
#define PIN_SDA   17
#define PIN_GPIO0 18
#define PIN_GPIO1 19
#define MASK_SCL   (1u << PIN_SCL)
#define MASK_SDA   (1u << PIN_SDA)
#define MASK_GPIO0 (1u << PIN_GPIO0)
#define MASK_GPIO1 (1u << PIN_GPIO1)
#define MASK_ALL (MASK_SCL | MASK_SDA | MASK_GPIO0 | MASK_GPIO1)
#define I2C_ADDRESS 0x42
#define REG_DIR   0x00
#define REG_OUT   0x01
#define REG_INPUT 0x02
#define REG_ID    0x03

static inline uint32_t gpio_read(void)
{
    return read_gpio_pins();
}

static inline void clear_pins(uint32_t mask)
{
    clear_gpio_pins_n(~mask);
}

static inline void sda_drive_low(void)
{
    clear_pins(MASK_SDA);
    set_output_pins(MASK_SDA);
}

static inline void sda_release(void)
{
    set_input_pins(MASK_SDA);
}

static void apply_gpio(uint8_t gpio_dir_reg, uint8_t gpio_out_reg)
{
    if (gpio_out_reg & 0x01) {
        set_gpio_pins(MASK_GPIO0);
    } else {
        clear_pins(MASK_GPIO0);
    }
    if (gpio_out_reg & 0x02) {
        set_gpio_pins(MASK_GPIO1);
    } else {
        clear_pins(MASK_GPIO1);
    }
    if (gpio_dir_reg & 0x01) {
        set_input_pins(MASK_GPIO0);
    } else {
        set_output_pins(MASK_GPIO0);
    }
    if (gpio_dir_reg & 0x02) {
        set_input_pins(MASK_GPIO1);
    } else {
        set_output_pins(MASK_GPIO1);
    }
}

static uint8_t read_register(uint8_t reg_addr, uint8_t gpio_dir_reg, uint8_t gpio_out_reg)
{
    uint8_t value = 0;
    switch (reg_addr & 0x03) {
    case REG_DIR:
        value = gpio_dir_reg & 0x03;
        break;
    case REG_OUT:
        value = gpio_out_reg & 0x03;
        break;
    case REG_INPUT:
    {
        uint32_t pins = gpio_read();
        if (pins & MASK_GPIO0) {
            value |= 0x01;
        }
        if (pins & MASK_GPIO1) {
            value |= 0x02;
        }
        break;
    }
    case REG_ID:
        value = 0x20;
        break;
    }
    return value;
}

static inline void tx_drive_bit(uint8_t value, uint8_t bit_index)
{
    if (value & (1u << bit_index)) {
        sda_release();
    } else {
        sda_drive_low();
    }
}

void main(void)
{
    uint32_t pins;
    uint32_t prev_scl;
    uint32_t prev_sda;
    uint8_t rx_shift;
    uint8_t bit_count;
    uint8_t byte_index;
    uint8_t reg_addr;
    uint8_t active;
    uint8_t write_mode;
    uint8_t waiting_stop;
    uint8_t pending_ack;
    uint8_t ack_phase;
    uint8_t ack_seen_high;
    uint8_t gpio_dir_reg;
    uint8_t gpio_out_reg;
    uint8_t read_start_pending;
    uint8_t tx_active;
    uint8_t tx_byte;
    uint8_t tx_bit_index;
    uint8_t tx_seen_high;
    uint8_t master_ack_wait;
    uint8_t master_ack_seen_high;
    set_gpio_mask(MASK_ALL);
    set_input_pins(MASK_SCL | MASK_SDA);
    gpio_dir_reg = 0x00;
    gpio_out_reg = 0x00;
    apply_gpio(gpio_dir_reg, gpio_out_reg);
    sda_release();
    rx_shift = 0;
    bit_count = 0;
    byte_index = 0;
    reg_addr = 0;
    active = 0;
    write_mode = 0;
    waiting_stop = 0;
    pending_ack = 0;
    ack_phase = 0;
    ack_seen_high = 0;
    read_start_pending = 0;
    tx_active = 0;
    tx_byte = 0;
    tx_bit_index = 0;
    tx_seen_high = 0;
    master_ack_wait = 0;
    master_ack_seen_high = 0;
    while (1) {
        pins = gpio_read();
        if ((pins & MASK_SCL) &&
            (pins & MASK_SDA)) {
            break;
        }
    }
    prev_scl = 1;
    prev_sda = 1;
    while (1) {
        uint32_t current;
        uint32_t scl;
        uint32_t sda;
        current = gpio_read();
        scl = (current & MASK_SCL) ? 1 : 0;
        sda = (current & MASK_SDA) ? 1 : 0;
        if (scl && prev_sda && !sda) {
            active = 1;
            write_mode = 0;
            waiting_stop = 0;
            rx_shift = 0;
            bit_count = 0;
            byte_index = 0;
            pending_ack = 0;
            ack_phase = 0;
            ack_seen_high = 0;
            read_start_pending = 0;
            tx_active = 0;
            tx_seen_high = 0;
            master_ack_wait = 0;
            master_ack_seen_high = 0;
            sda_release();
        }
        if (scl && !prev_sda && sda) {
            active = 0;
            waiting_stop = 0;
            pending_ack = 0;
            ack_phase = 0;
            ack_seen_high = 0;
            read_start_pending = 0;
            tx_active = 0;
            tx_seen_high = 0;
            master_ack_wait = 0;
            master_ack_seen_high = 0;
            sda_release();
        }
        if (!prev_scl && scl) {
            if (ack_phase) {
                ack_seen_high = 1;
            }
            else if (tx_active) {
                tx_seen_high = 1;
            }
            else if (master_ack_wait) {
                master_ack_seen_high = 1;
                if (sda) {
                    waiting_stop = 1;
                }
            }
            else if (active && !waiting_stop && bit_count < 8) {
                rx_shift <<= 1;
                if (sda) {
                    rx_shift |= 1;
                }
                bit_count++;
                if (bit_count == 8) {
                    uint8_t accept_byte = 0;
                    if (byte_index == 0) {
                        uint8_t address;
                        uint8_t rw;
                        address = rx_shift >> 1;
                        rw = rx_shift & 1;
                        if (address == I2C_ADDRESS) {
                            if (rw == 0) {
                                write_mode = 1;
                                byte_index = 1;
                                accept_byte = 1;
                            } else {
                                write_mode = 0;
                                tx_byte = read_register(reg_addr, gpio_dir_reg, gpio_out_reg);
                                read_start_pending = 1;
                                accept_byte = 1;
                            }
                        } else {
                            active = 0;
                        }
                    }
                    else if (write_mode && byte_index == 1) {
                        reg_addr = rx_shift & 0x03;
                        byte_index = 2;
                        accept_byte = 1;
                    }
                    else if (write_mode && byte_index == 2) {
                        uint8_t data;
                        data = rx_shift;
                        switch (reg_addr & 0x03) {
                        case REG_DIR:
                            gpio_dir_reg = data & 0x03;
                            apply_gpio(gpio_dir_reg, gpio_out_reg);
                            break;
                        case REG_OUT:
                            gpio_out_reg = data & 0x03;
                            apply_gpio(gpio_dir_reg, gpio_out_reg);
                            break;
                        case REG_INPUT:
                            break;
                        case REG_ID:
                            break;
                        }
                        byte_index = 3;
                        waiting_stop = 1;
                        accept_byte = 1;
                    }
                    if (accept_byte) {
                        pending_ack = 1;
                    }
                }
            }
        }
        if (prev_scl && !scl) {
            if (pending_ack && !ack_phase) {
                sda_drive_low();
                pending_ack = 0;
                ack_phase = 1;
                ack_seen_high = 0;
            }
            else if (ack_phase && ack_seen_high) {
                sda_release();
                ack_phase = 0;
                ack_seen_high = 0;
                rx_shift = 0;
                bit_count = 0;
                if (read_start_pending) {
                    read_start_pending = 0;
                    tx_active = 1;
                    tx_bit_index = 7;
                    tx_seen_high = 0;
                    tx_drive_bit(tx_byte, tx_bit_index);
                }
            }
            else if (tx_active && tx_seen_high) {
                tx_seen_high = 0;
                if (tx_bit_index > 0) {
                    tx_bit_index--;
                    tx_drive_bit(tx_byte, tx_bit_index);
                } else {
                    sda_release();
                    tx_active = 0;
                    master_ack_wait = 1;
                    master_ack_seen_high = 0;
                }
            }
            else if (master_ack_wait && master_ack_seen_high) {
                master_ack_wait = 0;
                master_ack_seen_high = 0;
                sda_release();
            }
        }
        prev_scl = scl;
        prev_sda = sda;
    }
}
