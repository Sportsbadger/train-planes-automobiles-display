from __future__ import annotations

from base64 import b85decode
from functools import lru_cache
from zlib import decompress

from PIL import Image


TRANSITION_IMAGE_SIZE: tuple[int, int] = (256, 64)
TRANSITION_IMAGE_DATA: dict[str, str] = {
    'train': (
        'c-oznv2NQi5QdLoVR{hCj2?pc6*2~BP`-'
        '+RynwbWLBza3U&$=G_Z6zCvqlZ6*gKLZiIgJ+Ilu<V&%FEZNS?jVt%ckQ?E4M=w!!@tw;R0O'
        ';H5wZJAgY77Ak;d6sXx%#vg+~h2I^b1AgNA31pZ$Bw<6a-'
        'I5#f`ZaN6C08V{iuekbOD;&@4e6v(92T4i4kB++0Izcrn3p@Gs`NN7xx{UEi4QZs%n|j>Dbj'
        ');{csYAyWNswh9mEUofS6(17xinFUx7c!FVUf%W}Qs8c$%f3p}X!Fwcq`50-'
        'wF4&W2~3Z$L*zU0jy=-'
        '$1<DYxtj@8`u8M2_D7xXjNUok=`ZZ7*<zVJoUG{F=R?JrA1rE{t!L>Fbhn&CMSV-'
        '@LoYxnRYO!#n4H!&Al$zxl?he@?&E^3_dTjcOVT<}a9^wt|`??5Bo8odG?)(D=DwKBh`RR1c'
        'yHdYOOWrx-LGU8;@bHj}s*q5^>L$(x(=gavBp5<kzlj2Ki4GVN=Cbk~7PP61<U&J|=w{*bFN'
        'LyY~9hM2#0==XtgVw=Lp!;ph^GmNPVm-ogFW_L+BP+4na-IKiAY(eDB9N~T5TkCB0TrLz=$}'
        '3E?;pMz*vXoz#vazx1)SF7KbNQzHJG$MFk4Mt3@%#1k(D`b5SRT2CG5Br`WAF;r%Xhf2{2z*'
        'euig'
    ),
    'adsb': (
        'c-qyJv5wR*5QfJ|ba8Q9LPxPuc3%J}w?Ik|#kD*D#T7JkTToCStxxobg8p<84-'
        'ya$V(|niD1vlaGvk>gwios!I<l*HH(wmj|Nj#M{D+6WhqvQtz8_9v<?Kmbx<0v*yKu$EnS{T'
        'GGMLi3bUNO@UH-a5!dJ9tu-IL~ujte-<&DRW4;E-'
        '#BEJaE<W4I85kCc#VRusbS3LB2&*i1^<?ot#<r4Wy#+sksrt;5ZJXbxJclYOR$h=N0FOgqQ>'
        'y+$HDt|rw_{i?0@;izIujyA@UMe3~ZSX_F_%KKEEPZDlPtVXYOC~ph4@c=du;jwTlLC-'
        'G#S6-mGmjk7tN^-'
        ';<pE)hwFI<YsemsgtucAu1zWfYZ*Vi0{@KBG#Kp%>0?I7Bi1{F#1HyDKV?K(#91wQTlgXcaQ'
        'K>{8lolO2P(JdOu#q#bEVP5q*MyCyrv6-'
        '6c**_I$MMi@`ySraUPzwwN9F@GTd<miCcGei4ri*@Y}7vPzXD!kfS^8@f_W47Zvj8Of=duy1'
        '?jdF@2`;*JmyNMz@>pU#NTM;_YmPKXh7tRd8jkLJt*c_ARAKk#B1cV?px$awl6kFcM~VwKW1'
        'Ku0TbU4SKrWQCHvDb^Tu-&lwZ$W1-yp(=2m~kNowZ62DP47!>3#Q1>gcbFp^~J4ch4E<EGX-'
        '=FbA&9E5AnzY^ae>x7ps;os@??Pi{PgCdU2yKo*No|}-v5ij8{+}(3TTlha5zX1(gV}b'
    ),
    'adsb-records': (
        'c-ocKO^%x|6ae67KnaVf!ZKAgfg@zuS;0}V>$2%SD$o=42%tH@Tp^=gfT$ZPS-Adr#?RoSsu'
        'u?7;~76=KLGxx#X>ZXJOX&+Ovom$br<%n@?O593i~FKk4*BW2>Vkm#^!X_Yw=5teaGe8nkRJ'
        '=_E^0Zy&SSo=IBCPvpc>~8Fp|^*UnT|s{A{+hFvd>T)((W)3wK9UPZ2*`5w);JDzACya#^Fc'
        'hAOwd*0&p1TQ>E^f*DE;D#R0UpaBkUT-'
        '~*wC7jr7w2)4x9FvvARA>@nT@pt#(1QY$@ezl#`%teb@Mr7JAm_N<+vCd^peEC5Z~H;qY^3f'
        'L-'
        '0+zS=$)>O;I8DY&^m{&wJ~vAs6L`9qW~2vVR&fjPf)5ZnZZ`81Kk5vP!NE<jgsY9Z4fA;2<-'
        'nuD7yX2I&J+lUH6*|1h98&MV@dMs!&EQTHEpe5yU~*KT^Xr)Mwz<@x~T3pePz{7Gh+tR3S6o'
        ')u(d^wj#B^o;nIj=B)Hg@3&g7iRo*e570+VeNRpPo$T7-'
        'UMq;1HN?!*7&%H2jA`fG0v!)1CIOWxN^o5vVV`u0#Z-l`Ni{{`b8w<eC#JuV3-'
        '(eMX={<e{o(<Ub&>lxW%D$`%b`mEQa|%qMyV1fWx$NJ*!7^Jp(=Fl^-'
        'k{)~DEW8|>;EE<@!9lls`r?t*E3HS;LEU|OF)8~1`~eLuJ=7XGb1_$U7bC-DhX'
    ),
    'plane-alert': (
        'c-'
        'p1Z!A^ug3;@uM5aD0~p7geQ^Ao&5{38E=G4V(I6SDC`IPQ^yhlwH77T9$<&{;P&*~9Rl3~gr'
        'tE{bA^d!Mv1&H$cceh+XR^M_(EF>hZJkQ=!27bdZk!@p7szq+Acse4LRIN>}0bvfm3zPt;G6'
        'VI0|7~JdmAE`q4ruz%P4#CD8$B4f}f(~lUO%4V2qCmcvri2DN)eGckp^Q4Ho-'
        'vPs`^B7|N1H3nUmOpLo5j(cao)sMoE*2kIl0|-$L--dY#)**3l*hKgBkrDFH7yW?4Wr@skgn'
        'JJfZ%vuQteIO%Bw5@@!n!X7*<C9o;6PLQ2eDId%*_ky|@JT2Fr<`P}JM<t;nCIp=JAzn`6=X'
        'E#Vb7r7IILtRU`Y&0KP(1l}H_Uu(#R?E5#`u@#c!YwL}^JLx~mz|tDT#8buevs*Uv3-'
        'iqGrpH&==^iF;X~*jGXLT`RAL1AJ(oI$$Kq1A!LbXY{fisjJK3IX&vfr)c~{}0<y{(Z&h_%n'
        '4fvn`07jq<m;'
    ),
}


@lru_cache(maxsize=len(TRANSITION_IMAGE_DATA))
def load_transition_image(mode: str) -> Image.Image:
    """Decode and cache the monochrome image for a transport mode.

    Args:
        mode: Canonical transport mode name.

    Returns:
        A one-bit Pillow image ready for the OLED canvas.

    Raises:
        ValueError: If the mode has no transition image.
    """
    encoded_image = TRANSITION_IMAGE_DATA.get(mode)
    if encoded_image is None:
        raise ValueError(f"Unsupported transition mode: {mode}")

    image_bytes = decompress(b85decode(encoded_image))
    return Image.frombytes("1", TRANSITION_IMAGE_SIZE, image_bytes)
