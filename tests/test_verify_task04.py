import io
from PIL import Image
import core_engine as ce

buf = io.BytesIO()
Image.new('RGB', (1600, 1200), color=(120, 180, 200)).save(buf, format='JPEG')
out_b, mime = ce.compress_image_to_jpeg_640(buf.getvalue())
im = Image.open(io.BytesIO(out_b))
print('size=', im.size, 'bytes=', len(out_b), 'mime=', mime)
assert max(im.size) <= 384 and mime == 'image/jpeg'
print("TASK_04_SPECIFIC_OK")

