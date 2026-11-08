"""Generate a small original vector-style pickaxe ICO using only the standard library."""
from pathlib import Path
import struct

SHAPES = [([(5,25),(8,28),(24,10),(21,7)], (126,76,37)),
          ([(6,25),(7,26),(23,9),(22,8)], (199,139,69)),
          ([(3,10),(8,5),(15,3),(21,4),(27,9),(30,16),(31,21),(25,15),(20,11),(15,8),(9,9)], (24,91,124)),
          ([(3,10),(8,5),(15,3),(21,4),(27,9),(22,8),(17,6),(11,7)], (97,218,239)),
          ([(15,7),(19,6),(24,10),(25,14),(21,11)], (48,154,186))]

def inside(x, y, polygon):
    odd = False
    for (ax,ay),(bx,by) in zip(polygon,polygon[1:]+polygon[:1]):
        if (ay>y)!=(by>y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            odd = not odd
    return odd

def bitmap(size):
    pixels = bytearray()
    mask = bytearray()
    for y in reversed(range(size)):
        row = []
        for x in range(size):
            samples = []
            for sy in range(4):
                for sx in range(4):
                    px,py = (x+(sx+.5)/4)*32/size, (y+(sy+.5)/4)*32/size
                    color = (20,40,57) if (px-16)**2+(py-16)**2 < 15.7**2 else None
                    for polygon,fill in SHAPES:
                        if inside(px,py,polygon):
                            color = fill
                    if color:
                        samples.append(color)
            alpha = round(len(samples)*255/16)
            rgb = [round(sum(c[k] for c in samples)/len(samples)) for k in range(3)] if samples else [0,0,0]
            pixels += bytes([rgb[2],rgb[1],rgb[0],alpha])
            row.append(0 if alpha else 1)
        stride = ((size+31)//32)*4
        packed = bytearray(stride)
        for i,bit in enumerate(row):
            packed[i//8] |= bit << (7-i%8)
        mask += packed
    return struct.pack('<IiiHHIIiiII',40,size,size*2,1,32,0,len(pixels),0,0,0,0)+pixels+mask

def main():
    sizes = [16,24,32,48,64]
    images = [bitmap(size) for size in sizes]
    offset = 6+16*len(sizes)
    entries = bytearray()
    for size,data in zip(sizes,images):
        entries += struct.pack('<BBBBHHII',size,size,0,0,1,32,len(data),offset)
        offset += len(data)
    Path(__file__).with_name('pickaxe.ico').write_bytes(struct.pack('<HHH',0,1,len(sizes))+entries+b''.join(images))

if __name__ == '__main__':
    main()
