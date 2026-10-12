import re,unicodedata,difflib
_T="臥舉啞鈴槓體撐練轉飛劃擴彎頸單雙側機開臺階騎車繩跳後腿蹲拉伸環壺擺盪翻劍箭腳跑遊泳瑜伽軸鬆屈臂"
_S="卧举哑铃杠体撑练转飞划扩弯颈单双侧机开台阶骑车绳跳后腿蹲拉伸环壶摆荡翻剑箭脚跑游泳瑜伽轴松屈臂"
assert len(_T)==len(_S)
T2S=str.maketrans(_T,_S)
_STRIP=re.compile(r"[\s\-_.,'’`!?·・/\\()（）、，。]+")
def norm(s:str)->str:
  s=unicodedata.normalize("NFKC",s or "").lower().translate(T2S)
  words=[w for w in re.split(r"[\s\-_]+",s) if w]
  out=[]
  for w in words:
    if re.fullmatch(r"[a-z]+",w) and len(w)>=3 and w.endswith("s") and not w.endswith(("ss","us")) :
      w=w[:-1]
    out.append(w)
  return _STRIP.sub("","".join(out))
def fuzzy(q:str, keys, n=5, cutoff=0.6):
  """Return [(key,score)] sorted desc."""
  res=[(k,difflib.SequenceMatcher(None,q,k).ratio()) for k in keys]
  return sorted([r for r in res if r[1]>=cutoff],key=lambda r:-r[1])[:n]
