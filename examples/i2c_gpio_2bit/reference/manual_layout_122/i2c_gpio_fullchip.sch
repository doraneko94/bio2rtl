v {xschem version=3.4.8RC file_version=1.3}
G {}
K {}
V {}
S {}
F {}
E {}
N 180 240 180 250 {lab=vdd}
N -50 0 -50 240 {lab=vdd}
N 180 350 180 360 {lab=vss}
N -40 120 -40 360 {lab=vss}
N -30 180 -30 320 {lab=#net1}
N -60 -0 120 -0 {lab=vdd}
N -50 240 180 240 {lab=vdd}
N -60 360 180 360 {lab=vss}
N -30 320 120 320 {lab=#net1}
N -40 120 120 120 {lab=vss}
N 420 160 420 180 {lab=#net1}
N -30 180 420 180 {lab=#net1}
N 110 60 120 60 {lab=#net2}
N 110 60 110 230 {lab=#net2}
N 110 230 240 230 {lab=#net2}
N 240 230 240 300 {lab=#net2}
N -60 280 120 280 {lab=sda}
N 40 50 40 80 {lab=#net3}
N 40 80 120 80 {lab=#net3}
N -10 100 -10 120 {lab=vss}
N 420 40 450 40 {lab=#net4}
N 450 0 450 40 {lab=#net4}
N 450 -0 480 -0 {lab=#net4}
N 420 20 480 20 {lab=#net5}
N 420 0 430 0 {lab=#net6}
N 430 -0 430 30 {lab=#net6}
N 430 30 480 30 {lab=#net6}
N 480 30 480 40 {lab=#net6}
N 420 60 480 60 {lab=#net7}
N 570 90 580 90 {lab=vss}
N 580 90 580 230 {lab=vss}
N 570 230 580 230 {lab=vss}
N 560 -30 570 -30 {lab=vdd}
N 560 -30 560 110 {lab=vdd}
N 560 110 570 110 {lab=vdd}
N -50 -30 560 -30 {lab=vdd}
N -50 -30 -50 -0 {lab=vdd}
N 180 360 570 360 {lab=vss}
N 570 230 570 360 {lab=vss}
N 420 100 450 100 {lab=#net8}
N 450 100 450 160 {lab=#net8}
N 450 160 480 160 {lab=#net8}
N 420 140 480 140 {lab=#net9}
N 420 80 460 80 {lab=#net10}
N 460 80 460 180 {lab=#net10}
N 460 180 480 180 {lab=#net10}
N 420 120 440 120 {lab=#net11}
N 440 120 440 200 {lab=#net11}
N 440 200 480 200 {lab=#net11}
N 670 -40 670 40 {lab=#net12}
N 660 40 670 40 {lab=#net12}
N 680 -50 680 180 {lab=#net13}
N 660 180 680 180 {lab=#net13}
N 100 -50 680 -50 {lab=#net13}
N 100 -50 100 20 {lab=#net13}
N 100 20 120 20 {lab=#net13}
N 110 40 120 40 {lab=#net12}
N 110 -40 110 40 {lab=#net12}
N 110 -40 670 -40 {lab=#net12}
N 100 100 120 100 {lab=scl}
N 660 20 690 20 {lab=gpio1}
N 660 160 690 160 {lab=gpio0}
C {i2c_gpio_core.sym} 270 80 0 0 {name=x1}
C {devices/iopin.sym} -60 0 0 1 {name=p1 lab=vdd}
C {devices/iopin.sym} -60 360 0 1 {name=p2 lab=vss}
C {../../../../technology/tr1um_hand_support_v1/sda_io.sym} 180 300 0 0 {name=x2}
C {devices/iopin.sym} -60 280 0 1 {name=p3 lab=sda}
C {../../../../technology/tr1um_hand_support_v1/por.sym} -10 50 0 0 {name=x3}
C {../../../../technology/tr1um_hand_support_v1/gpio_io.sym} 570 30 0 0 {name=x4}
C {../../../../technology/tr1um_hand_support_v1/gpio_io.sym} 570 170 0 0 {name=x5}
C {devices/ipin.sym} 100 100 0 0 {name=p4 lab=scl}
C {devices/iopin.sym} 690 20 0 0 {name=p5 lab=gpio1}
C {devices/iopin.sym} 690 160 0 0 {name=p6 lab=gpio0}
