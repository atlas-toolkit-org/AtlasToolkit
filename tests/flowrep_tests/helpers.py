import numpy as np

from flowrep3d import from_arrays


def grid(n=7,height=None):
    V=np.array([(x,y,0. if height is None else height(x,y)) for y in np.linspace(-1,1,n) for x in np.linspace(-1,1,n)])
    F=[[j*n+i,j*n+i+1,(j+1)*n+i+1,(j+1)*n+i] for j in range(n-1) for i in range(n-1)]
    return from_arrays(V,F)


def torus(n=16,m=8):
    V=[]
    for i in range(n):
        a=i*2*np.pi/n
        for j in range(m):
            b=j*2*np.pi/m
            V.append([(2+0.6*np.cos(b))*np.cos(a),(2+0.6*np.cos(b))*np.sin(a),0.6*np.sin(b)])
    F=[[i*m+j,((i+1)%n)*m+j,((i+1)%n)*m+(j+1)%m,i*m+(j+1)%m] for i in range(n) for j in range(m)]
    return from_arrays(V,F)


def sphere(n=16,m=8):
    V=[[0,0,1.]]
    for j in range(1,m):
        b=np.pi*j/m
        for i in range(n):
            a=2*np.pi*i/n
            V.append([np.sin(b)*np.cos(a),np.sin(b)*np.sin(a),np.cos(b)])
    V.append([0,0,-1.]); bottom=len(V)-1
    F=[[0,1+i,1+(i+1)%n] for i in range(n)]
    for j in range(m-2):
        F.extend([[1+j*n+i,1+(j+1)*n+i,1+(j+1)*n+(i+1)%n,1+j*n+(i+1)%n] for i in range(n)])
    F.extend([[bottom,1+(m-2)*n+(i+1)%n,1+(m-2)*n+i] for i in range(n)])
    return from_arrays(V,F)


def cylinder(n=16,levels=5):
    V=[[np.cos(i*2*np.pi/n),np.sin(i*2*np.pi/n),z] for z in np.linspace(-1,1,levels) for i in range(n)]
    F=[[j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i] for j in range(levels-1) for i in range(n)]
    return from_arrays(V,F)

