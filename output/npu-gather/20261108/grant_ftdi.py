from pathlib import Path
import grp,os
matches=[]
for p in Path('/sys/bus/usb/devices').iterdir():
    try:
        if ((p/'idVendor').read_text().strip(),(p/'idProduct').read_text().strip(),
            (p/'serial').read_text().strip())==('0403','6014','210512180081'):
            matches.append(Path(f'/dev/bus/usb/{int((p/"busnum").read_text()):03d}/{int((p/"devnum").read_text()):03d}'))
    except FileNotFoundError:
        pass
assert len(matches)==1,matches
node=matches[0]
os.chown(node,-1,grp.getgrnam('plugdev').gr_gid)
os.chmod(node,0o660)
print('Granted plugdev 660 to verified FT232H serial 210512180081:',node)
