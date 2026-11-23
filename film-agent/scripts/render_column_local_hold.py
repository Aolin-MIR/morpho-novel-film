"""Make a measured column-bound local hold from this exact H3 plate; no GPU."""
from pathlib import Path
import json,hashlib,subprocess,argparse
import cv2
import numpy as np
parser=argparse.ArgumentParser();parser.add_argument('--hold-frame',type=int,default=28);parser.add_argument('--output',default='held.mp4');args=parser.parse_args();assert 1<=args.hold_frame<124 and Path(args.output).name==args.output and args.output.endswith('.mp4')
root=Path(__file__).resolve().parents[1];folder=root/'artifacts/aesthetic-reselection/h3/impact-expansion/time-stops-at-column';source=folder/'shot.mp4';output=folder/args.output
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(source)=='15067f78d7c508fd6ec821caaa5b933e3c80da9c8f2fc06b47c9bc79cc4b4b60';assert not output.exists()
cv2.setNumThreads(2);cap=cv2.VideoCapture(str(source));fps=cap.get(cv2.CAP_PROP_FPS);frames=[]
while True:
 ok,f=cap.read()
 if not ok:break
 frames.append(f)
cap.release();assert len(frames)==124 and fps==24
h,w=frames[0].shape[:2];start=args.hold_frame;ref=frames[start];gray=cv2.cvtColor(ref,cv2.COLOR_BGR2GRAY)
mask=np.zeros((h,w),np.uint8);mask[32:670,615:712]=255
points=cv2.goodFeaturesToTrack(gray,maxCorners=200,qualityLevel=.02,minDistance=7,mask=mask);assert points is not None and len(points)>=20
p0=points.copy();previous=gray;current=points.copy();index=np.arange(len(points));transforms={start:np.float32([[1,0,0],[0,1,0]])};records=[]
for n in range(start+1,len(frames)):
 now=cv2.cvtColor(frames[n],cv2.COLOR_BGR2GRAY);tracked,status,error=cv2.calcOpticalFlowPyrLK(previous,now,current,None,winSize=(25,25),maxLevel=3)
 good=status.ravel().astype(bool)&(error.ravel()<35);index=index[good];tracked=tracked[good];assert len(index)>=12,(n,len(index))
 transform,inliers=cv2.estimateAffinePartial2D(p0[index].reshape(-1,2),tracked.reshape(-1,2),method=cv2.RANSAC,ransacReprojThreshold=1.5,maxIters=2000,confidence=.995)
 assert transform is not None and int(inliers.sum())>=10,n
 projected=cv2.transform(p0[index],transform).reshape(-1,2);residual=np.linalg.norm(projected-tracked.reshape(-1,2),axis=1);med=float(np.median(residual[inliers.ravel().astype(bool)]));assert med<1.2,(n,med)
 transforms[n]=transform;records.append({'frame':n,'inliers':int(inliers.sum()),'median_error_px':med,'transform':transform.tolist()});previous=now;current=tracked.reshape(-1,1,2)
alpha=np.repeat(np.clip((670-np.arange(w))/12,0,1)[None,:],h,axis=0).astype(np.float32)
command=['ffmpeg','-v','error','-nostdin','-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{w}x{h}','-r','24','-i','-','-i',str(source),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','15','-preset','fast','-threads','3','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(output)]
with subprocess.Popen(command,stdin=subprocess.PIPE) as enc:
 for n,frame in enumerate(frames):
  if n<=start:out=frame
  else:
   t=transforms[n];held=cv2.warpAffine(ref,t,(w,h),flags=cv2.INTER_LINEAR);a=cv2.warpAffine(alpha,t,(w,h),flags=cv2.INTER_LINEAR);valid=cv2.warpAffine(np.ones((h,w),np.float32),t,(w,h),flags=cv2.INTER_LINEAR);a*=valid
   out=np.rint(held.astype(np.float32)*a[:,:,None]+frame.astype(np.float32)*(1-a[:,:,None])).clip(0,255).astype(np.uint8);assert np.array_equal(out[a==0],frame[a==0])
  enc.stdin.write(out.tobytes())
 enc.stdin.close();assert enc.wait()==0
subprocess.run(['ffmpeg','-v','error','-nostdin','-xerror','-i',str(output),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
def audio(p):return subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:a:0','-c:a','copy','-f','streamhash','-hash','sha256','-'],text=True).strip()
assert audio(output)==audio(source)
receipt={'production':'cpu-edit','source':'shot.mp4','source_sha256':sha(source),'output':args.output,'output_sha256':sha(output),'fps':24,'frames':124,'held_source_frame':start,'held_frame_range':[start+1,124],'boundary_in_reference_pixels':{'solid_left_to_x':658,'feather_to_x':670,'occluder':'central steel column'},'tracking':'Pyramidal LK on column-only stationary texture; RANSAC partial affine camera compensation; no object motion used','tracking_frames':records,'max_median_error_px':max(x['median_error_px'] for x in records),'unchanged':'native current frame outside warped left hold mask, exact equality before encoding','audio':'source AAC packets copied; not auditioned','scope':'left region held in scene coordinates while right actor and rain keep native time; not proof of physical time laws or character crossing the boundary'}
output.with_suffix('.receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'output_sha256':receipt['output_sha256'],'frames':124,'max_median_error_px':receipt['max_median_error_px']}))
