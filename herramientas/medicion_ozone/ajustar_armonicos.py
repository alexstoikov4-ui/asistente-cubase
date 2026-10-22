"""Ajusta, para Tape, Triode y Dual Triode, una curva estática por Amount (nudos + PCHIP) que reproduce la
ganancia y los armónicos H2-H5 de Ozone 7 (seno de 1 kHz a -24, -12, -6 y -0,1 dBFS). Esos perfiles de Ozone
tienen memoria (histéresis, polarización que sigue al nivel) y su curva medida no basta.

Uso: python ajustar_armonicos.py carpeta_con_los_wav tape triode dual   -> ajustes_ozone7.json"""
import sys, json, glob, numpy as np
from scipy.optimize import least_squares
from scipy.interpolate import PchipInterpolator
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import generar_curvas as G, analizar as A
FS=44100; N=4410; t=np.arange(N)/FS
LEVELS=[-24,-12,-6,-0.1]
KN=np.array([0.02,0.04,0.063,0.1,0.16,0.25,0.35,0.5,0.7,1.0,1.3])
KX=np.concatenate([-KN[::-1],[0],KN])
LAMBDA=0.4  # suavidad: castiga los cambios bruscos de pendiente entre tramos
def values(p):
    """p: log de la pendiente de cada tramo entre nudos -> curva creciente con y(0)=0"""
    s=np.exp(p); dx=np.diff(KX); inc=s*dx; m=len(KN)
    left=-np.cumsum(inc[:m][::-1])[::-1]; right=np.cumsum(inc[m:])
    return np.concatenate([left,[0],right])
def curve(p):
    return PchipInterpolator(KX,values(p),extrapolate=True)
def meas(y,f=1000):
    X=np.fft.rfft(y)*2/N
    return np.array([np.abs(X[int(round(f*h*N/FS))]) for h in range(1,6)])
def resid(v,tgt):
    c=curve(v); r=[]
    for lv in LEVELS:
        if lv not in tgt: continue
        a=10**(lv/20); x=a*np.sin(2*np.pi*1000*t); m=meas(c(x))
        gain=20*np.log10(max(m[0],1e-9)/a); hd=20*np.log10(np.maximum(m[1:],1e-12)/max(m[0],1e-9))
        tg,th=tgt[lv]
        r.append(2.0*(gain-tg))
        for mh,thh in zip(hd,th[:4]):
            if thh is not None and thh>-70: r.append(mh-thh)
            else: r.append(0.3*max(0,mh+68))
    return np.concatenate([r,LAMBDA*np.diff(v)])
def target(fn):
    d=json.load(open(fn)); return {s['nivel']:(s['ganancia_db'],s['armonicos_db']) for s in d['senos'] if s['f']==1000}
ref=A.load(G.os.path.join(G.HERE,'senal_prueba_ozone.wav')); mp=json.load(open(G.os.path.join(G.HERE,'senal_prueba_ozone.json')))
P={'tape':('tape_al_5','prueba_tape.',24.0),'triode':('prueba_triode_al_5','prueba_triode.',50.0),'dual':('dual_triode_al_5','dual_triode.',49.4)}
out={}
folder=sys.argv[1]
for name in sys.argv[2:]:
    out[name]={}
    for amt,key in ((5,P[name][0]),(15,P[name][1])):
        fn=G.find(folder,key); tgt=target(fn.replace('.wav','_medidas.json'))
        X,Y=G.static_curve(ref,mp,A.load(fn),P[name][2],False,(-0.1,))
        # arranque: pendientes de la curva medida (suavizadas y siempre positivas)
        ym=np.interp(KX,X,Y); sl=np.maximum(np.diff(ym)/np.diff(KX),1e-3)
        v0=np.log(np.convolve(np.pad(sl,1,mode='edge'),[1/3,1/3,1/3],'valid'))
        best=None
        for sh in (0.0,-0.5,0.5):
            r=least_squares(resid,v0+sh,args=(tgt,),method='trf',max_nfev=3000)
            if best is None or r.cost<best.cost: best=r
        e0=np.sum(resid(v0,tgt)**2)/2
        print(f"{name} {amt}: coste inicial (curva medida) {e0:.1f} -> ajustada {best.cost:.1f}",flush=True)
        r=resid(best.x,tgt); c=curve(best.x)
        for lv in LEVELS:
            a=10**(lv/20); m=meas(c(a*np.sin(2*np.pi*1000*t)))
            g=20*np.log10(m[0]/a); hd=20*np.log10(np.maximum(m[1:],1e-12)/m[0]); tg,th=tgt[lv]
            print(f"   {lv:6.1f} dBFS ganancia {g:+6.2f}/{tg:+6.2f}  "+"  ".join(f"H{h+2} {hd[h]:6.1f}/{(th[h] if th[h] is not None else -999):6.1f}" for h in range(4)))
        out[name][amt]=best.x.tolist()
dst=G.os.path.join(G.HERE,'ajustes_ozone7.json')
old=json.load(open(dst)) if G.os.path.exists(dst) else {}
old.update(out)
json.dump(old,open(dst,'w'),indent=1)
