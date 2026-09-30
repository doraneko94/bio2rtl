v {xschem version=3.4.8RC file_version=1.3}
G {}
K {}
V {}
S {}
F {}
E {}
N 0 40 50 40 {lab=DIR_B}
N 40 40 40 180 {lab=DIR_B}
N 40 180 50 180 {lab=DIR_B}
N 0 100 50 100 {lab=OUT}
N 50 80 50 100 {lab=OUT}
N 0 80 20 80 {lab=OUT_B}
N 20 80 20 220 {lab=OUT_B}
N 20 220 50 220 {lab=OUT_B}
N 180 200 200 200 {lab=#net1}
N 0 0 240 0 {lab=VDD}
N 240 0 240 30 {lab=VDD}
N 240 20 250 20 {lab=VDD}
N 240 60 250 60 {lab=VDD}
N 250 20 250 60 {lab=VDD}
N 240 90 240 170 {lab=PAD}
N 240 120 310 120 {lab=PAD}
N 30 120 100 120 {lab=VSS}
N 30 120 30 260 {lab=VSS}
N 240 230 240 260 {lab=VSS}
N 240 200 250 200 {lab=VSS}
N 240 240 250 240 {lab=VSS}
N 250 200 250 240 {lab=VSS}
N 10 0 10 140 {lab=VDD}
N 310 120 550 120 {lab=PAD}
N 10 140 100 140 {lab=VDD}
N 0 260 240 260 {lab=VSS}
C {devices/iopin.sym} 0 0 0 1 {name=p1 lab=VDD}
C {devices/iopin.sym} 0 260 0 1 {name=p2 lab=VSS}
C {devices/ipin.sym} 0 60 0 0 {name=p3 lab=DIR}
C {devices/ipin.sym} 0 40 0 0 {name=p4 lab=DIR_B}
C {devices/ipin.sym} 0 100 0 0 {name=p5 lab=OUT}
C {devices/ipin.sym} 0 80 0 0 {name=p6 lab=OUT_B}
C {devices/iopin.sym} 550 120 0 0 {name=p7 lab=PAD}
C {TR-1um_5_stdcell/NAND2.sym} 70 60 0 0 {name=x1}
C {TR-1umLIB/MP.sym} 200 60 0 0 {name=XM1
model=PMOS
w=30u
l=1u
m=27
spiceprefix=X
as=0
ad=0
ps=0
pd=0
nrd=0
nrs=0}
C {TR-1um_5_stdcell/AND2_X1.sym} 70 200 0 0 {name=x2}
C {TR-1umLIB/MN.sym} 200 200 0 0 {name=XM2
model=NMOS
w=30u
l=1u
m=9
spiceprefix=X
as=0
ad=0
ps=0
pd=0
nrd=0
nrs=0}
