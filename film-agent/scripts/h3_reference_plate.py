"""Derive an H3 reference from a verified source image or a numbered video frame."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import subprocess

from PIL import Image, ImageOps

from film_source_windows import require_archived_window, require_clean_source_window
from media_segments import fingerprint


def derive_plate(spec, *, review_archive=None):
    """crop_xywh uses displayed pixels; frame is zero-based in decoder presentation order."""
    permitted = {'source', 'sha256', 'crop_xywh', 'frame', 'approved_frames'}
    if not isinstance(spec, dict) or not {'source', 'sha256', 'crop_xywh'} <= spec.keys() <= permitted:
        raise ValueError('派生参考须声明原片、原片 SHA-256、像素裁切区，视频另带从零起的帧号。')
    path = Path(spec['source']).resolve(strict=True)
    before = path.stat()
    digest = fingerprint(path)
    if spec['sha256'] != digest:
        raise ValueError(f'参考来源与已选择的原片版本不同：{path.name}')
    rect = spec['crop_xywh']
    if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError('参考裁切使用整数像素 x、y、宽、高。')
    x, y, width, height = rect
    frame = spec.get('frame')
    approved = spec.get('approved_frames')
    if 'approved_frames' in spec:
        if (not isinstance(approved, list) or len(approved) != 2
                or any(type(point) is not int for point in approved)
                or type(frame) is not int or not 0 <= approved[0] <= frame < approved[1]):
            raise ValueError('交接帧必须位于同一原片已确认的可用帧区间。')
    if 'frame' in spec:
        if type(frame) is not int or frame < 0:
            raise ValueError('视频帧号须为从零起的整数。')
        # Production archives keep the review ledger alongside their media directories.
        require_archived_window(path, digest, frame, frame + 1)
        if review_archive is not None:
            require_clean_source_window(review_archive, digest, frame, frame + 1)
        # Decode sequentially to select the exact displayed frame even for variable-rate material.
        data = subprocess.check_output(
            ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:v:0',
             '-vf', f'trim=start_frame={frame}:end_frame={frame + 1}', '-frames:v', '1',
             '-c:v', 'png', '-f', 'image2pipe', '-'], timeout=120)
        if not data:
            raise ValueError(f'原片中没有第 {frame} 帧。')
        image_source = BytesIO(data)
    else:
        image_source = path
    with Image.open(image_source) as image:
        if getattr(image, 'n_frames', 1) != 1:
            raise ValueError('动态素材须明确选择一帧。')
        visible = ImageOps.exif_transpose(image)
        if not (x >= 0 and y >= 0 and width > 0 and height > 0 and
                x + width <= visible.width and y + height <= visible.height):
            raise ValueError('参考裁切须在原始可见画面之内。')
        stream = BytesIO()
        visible.convert('RGB').crop((x, y, x + width, y + height)).save(stream, format='PNG')
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('提取期间参考源发生变更，请重新选取原片版本。')
    contents = stream.getvalue()
    return contents, {'source': path.name, 'source_sha256': digest, 'crop_xywh': rect,
                      **({'frame': frame} if frame is not None else {}),
                      **({'approved_frames': approved} if approved is not None else {}),
                      'plate_sha256': sha256(contents).hexdigest()}
