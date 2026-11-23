"""Validate reference intent and native face detail before allocating render time."""
from io import BytesIO
from PIL import Image, ImageFilter, ImageStat


def check_reference(picture, source):
    role = picture['role']
    scope = picture.get('people')
    if role in ('location', 'prop') and scope != 'none':
        raise ValueError('场景和道具参考须为干净底图，无可识别人物。')
    if role == 'composition' and scope not in ('none', 'distant', 'readable'):
        raise ValueError('构图参考须标明人物是远景比例还是需要辨认。')
    if role == 'character' and scope != 'single':
        raise ValueError('人物参考须隔离出单一角色。')
    required = role == 'character' or scope == 'readable'
    boxes = picture.get('face_boxes', [])
    if not isinstance(boxes, list) or (required and not boxes) or (role == 'character' and len(boxes) != 1):
        raise ValueError('人物和辨认表情的构图参考须标注原始像素脸框。')
    if not boxes:
        return
    with Image.open(BytesIO(source) if isinstance(source, bytes) else source) as image:
        for box in boxes:
            check_face(image, box)


def check_face(image, box):
    if (not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box)
            or not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height)):
        raise ValueError('脸框超出原始图像。')
    left, top, right, bottom = box
    if min(right - left, bottom - top) < 80:
        raise ValueError('脸部源像素短边不足 80 px，请换原生中近景参考。')
    face = image.crop(box).convert('L')
    detail = face.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128))
    # Ignore crop and kernel boundaries. This rejects gross blur, not identity mistakes.
    center = detail.crop((face.width // 4, face.height // 4, 3 * face.width // 4, 3 * face.height // 4))
    if ImageStat.Stat(center).var[0] < 12:
        raise ValueError('脸部中心细节不足，请换眼鼻清晰的原始参考。')
