v {xschem version=3.4.8RC file_version=1.3}
G {}
K {}
V {}
S {}
F {}
E {}
N -160 -60 170 -60 {lab=VDD}
N 50 -60 50 -40 {lab=VDD}
N 170 -60 170 -40 {lab=VDD}
N -160 140 170 140 {lab=VSS}
N 170 40 170 140 {lab=VSS}
N 50 40 50 140 {lab=VSS}
N 0 110 0 140 {lab=VSS}
N 0 80 50 80 {lab=VSS}
N -110 120 -110 140 {lab=VSS}
N -110 -60 -110 40 {lab=VDD}
N -160 0 0 0 {lab=PAD}
N -0 0 0 50 {lab=PAD}
C {devices/iopin.sym} -160 -60 0 1 {name=p1 lab=VDD}
C {devices/iopin.sym} -160 140 0 1 {name=p2 lab=VSS}
C {devices/iopin.sym} -160 0 0 1 {name=p3 lab=PAD}
C {devices/opin.sym} 240 0 0 0 {name=p4 lab=IN}
C {TR-1um_5_stdcell/BUF_X1.sym} 20 0 0 0 {name=x1}
C {TR-1um_5_stdcell/BUF_X2.sym} 140 0 0 0 {name=x2}
C {TR-1um_5_stdcell/BUF_X4.sym} -140 80 0 0 {name=x3}
C {TR-1umLIB/MN.sym} -40 80 0 0 {name=XM1
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
C {devices/ipin.sym} -160 80 0 0 {name=p5 lab=LOW}
C {devices/lab_pin.sym} -40 80 1 0 {name=p6 sig_type=std_logic lab=ng}
