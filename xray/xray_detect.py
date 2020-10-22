import paddlex as pdx
import numpy as np
import cv2
import os
import colorsys

from flask import Response
from flask import Flask
from flask import render_template
import threading
import argparse
import datetime
import imutils
from imutils.video import FileVideoStream
import time
import cv2
import paddlehub as hub


outputFrame = None
outputFrame_right = None
lock = threading.Lock()
print(lock)
# initialize a flask object
app = Flask(__name__)


def get_color_map_list(num_classes):
    """ Returns the color map for visualizing the segmentation mask,
        which can support arbitrary number of classes.
    Args:
        num_classes: Number of classes
    Returns:
        The color map
    """
    color_map = num_classes * [0, 0, 0]
    for i in range(0, num_classes):
        j = 0
        lab = i
        while lab:
            color_map[i * 3] |= (((lab >> 0) & 1) << (7 - j))
            color_map[i * 3 + 1] |= (((lab >> 1) & 1) << (7 - j))
            color_map[i * 3 + 2] |= (((lab >> 2) & 1) << (7 - j))
            j += 1
            lab >>= 3
    color_map = [color_map[i:i + 3] for i in range(0, len(color_map), 3)]
    return color_map


def draw_bbox_mask(image, results, threshold=0.5):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib as mpl
    import matplotlib.figure as mplfigure
    import matplotlib.colors as mplc
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    # refer to  https://github.com/facebookresearch/detectron2/blob/master/detectron2/utils/visualizer.py
    def _change_color_brightness(color, brightness_factor):
        assert brightness_factor >= -1.0 and brightness_factor <= 1.0
        color = mplc.to_rgb(color)
        polygon_color = colorsys.rgb_to_hls(*mplc.to_rgb(color))
        modified_lightness = polygon_color[1] + (brightness_factor *
                                                 polygon_color[1])
        modified_lightness = 0.0 if modified_lightness < 0.0 else modified_lightness
        modified_lightness = 1.0 if modified_lightness > 1.0 else modified_lightness
        modified_color = colorsys.hls_to_rgb(
            polygon_color[0], modified_lightness, polygon_color[2])
        return modified_color

    _SMALL_OBJECT_AREA_THRESH = 1000
    # setup figure
    width, height = image.shape[1], image.shape[0]
    scale = 1
    fig = mplfigure.Figure(frameon=False)
    dpi = fig.get_dpi()
    fig.set_size_inches(
        (width * scale + 1e-2) / dpi,
        (height * scale + 1e-2) / dpi, )
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.axis("off")
    ax.set_xlim(0.0, width)
    ax.set_ylim(height)
    default_font_size = max(np.sqrt(height * width) // 90, 10 // scale)
    linewidth = max(default_font_size / 4, 1)

    labels = list()
    for dt in np.array(results):
        if dt['category'] not in labels:
            labels.append(dt['category'])
    color_map = get_color_map_list(256)

    keep_results = []
    areas = []
    for dt in np.array(results):
        cname, bbox, score = dt['category'], dt['bbox'], dt['score']
        if score < threshold:
            continue
        keep_results.append(dt)
        areas.append(bbox[2] * bbox[3])
    areas = np.asarray(areas)
    sorted_idxs = np.argsort(-areas).tolist()
    keep_results = [keep_results[k]
                    for k in sorted_idxs] if len(keep_results) > 0 else []

    for dt in np.array(keep_results):
        cname, bbox, score = dt['category'], dt['bbox'], dt['score']
        xmin, ymin, w, h = bbox
        xmax = xmin + w
        ymax = ymin + h

        color = tuple(color_map[labels.index(cname) + 2])
        color = [c / 255. for c in color]
        # draw bbox
        ax.add_patch(
            mpl.patches.Rectangle(
                (xmin, ymin),
                w,
                h,
                fill=False,
                edgecolor=color,
                linewidth=linewidth * scale,
                alpha=0.8,
                linestyle="-", ))

        # draw mask
        if 'mask' in dt:
            mask = dt['mask']
            mask = np.ascontiguousarray(mask)
            res = cv2.findContours(
                mask.astype("uint8"), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
            hierarchy = res[-1]
            alpha = 0.5
            if hierarchy is not None:
                has_holes = (hierarchy.reshape(-1, 4)[:, 3] >= 0).sum() > 0
                res = res[-2]
                res = [x.flatten() for x in res]
                res = [x for x in res if len(x) >= 6]
                for segment in res:
                    segment = segment.reshape(-1, 2)
                    edge_color = mplc.to_rgb(color) + (1,)
                    polygon = mpl.patches.Polygon(
                        segment,
                        fill=True,
                        facecolor=mplc.to_rgb(color) + (alpha,),
                        edgecolor=edge_color,
                        linewidth=max(default_font_size // 15 * scale, 1), )
                    ax.add_patch(polygon)

        # draw label
        text_pos = (xmin, ymin)
        horiz_align = "left"
        instance_area = w * h
        if (instance_area < _SMALL_OBJECT_AREA_THRESH * scale or
                h < 40 * scale):
            if ymin >= height - 5:
                text_pos = (xmin, ymin)
            else:
                text_pos = (xmin, ymax)
        height_ratio = h / np.sqrt(height * width)
        font_size = (np.clip((height_ratio - 0.02) / 0.08 + 1, 1.2,
                             2) * 0.5 * default_font_size)
        text = "{} {:.2f}".format(cname, score)
        color = np.maximum(list(mplc.to_rgb(color)), 0.2)
        color[np.argmax(color)] = max(0.8, np.max(color))
        color = _change_color_brightness(color, brightness_factor=0.7)
        ax.text(
            text_pos[0],
            text_pos[1],
            text,
            size=font_size * scale,
            family="sans-serif",
            bbox={
                "facecolor": "black",
                "alpha": 0.8,
                "pad": 0.7,
                "edgecolor": "none"
            },
            verticalalignment="top",
            horizontalalignment=horiz_align,
            color=color,
            zorder=10,
            rotation=0, )

    s, (width, height) = canvas.print_to_buffer()
    buffer = np.frombuffer(s, dtype="uint8")

    img_rgba = buffer.reshape(height, width, 4)
    rgb, alpha = np.split(img_rgba, [3], axis=2)

    try:
        import numexpr as ne
        visualized_image = ne.evaluate(
            "image * (1 - alpha / 255.0) + rgb * (alpha / 255.0)")
    except ImportError:
        alpha = alpha.astype("float32") / 255.0
        visualized_image = image * (1 - alpha) + rgb * alpha

    visualized_image = visualized_image.astype("uint8")

    return visualized_image


def visualize(image, result, threshold=0.5, img_num=0, save_dir='./'):
    """
        Visualize bbox and mask results
    """

    if isinstance(image, np.ndarray):
        image_name = str(img_num) + '.jpg'
    else:
        image_name = os.path.split(image)[-1]
        image = cv2.imread(image)

    image = draw_bbox_mask(image, result, threshold=threshold)
    if save_dir is not None:
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        out_path = os.path.join(save_dir, '{}'.format(image_name))
        cv2.imwrite(out_path, image)
        print('The visualized result is saved as {}'.format(out_path))
    else:
        return image

path1 = '/Volumes/Macintosh HD - 数据/stream-video-browser/xray/'
print(path1)
predictor = pdx.deploy.Predictor(path1 + 'inference_model', use_gpu=False)
cap = FileVideoStream(path1 + 'data/xray.mp4').start()
cap_right = FileVideoStream(path1 + 'data/dji2.mp4').start()
time.sleep(2.0)

module=hub.Module(name="yolov3_mobilenet_v1_coco2017")


@app.route("/")
def index():
    # return the rendered template
    return render_template("index.html")

def detect_motion(frameCount):
    global cap, cap_right, outputFrame, outputFrame_right, lock
    total = 0
    # /Volumes/Macintosh HD - 数据/wsp_vue_1/stream-video-browser 2/xray/data/xray.mp4
    i = 1
    # while cap.isOpened():
    while True:
        frame = cap.read()
        frame_right = cap_right.read()

        result = predictor.predict(frame)
        result_right = predictor.predict(frame_right)

        print(i)
        vis_img = visualize(frame, result, threshold=0.5, img_num=i, save_dir=None)
        vis_img_right = visualize(frame_right, result_right, threshold=0.5, img_num=i, save_dir=None)
        i += 1
        # cv2.imshow('xray', vis_img)
        # if cv2.waitKey(1) & 0xFF == ord('q'):
        #     break

        with lock:
            outputFrame = vis_img.copy()
            outputFrame_right = vis_img_right.copy()
    # cap.release()
    # cv2.destroyAllWindows()


def generate(isRight=False):
    # grab global references to the output frame and lock variables
    global outputFrame, outputFrame_right, lock
    print(123123123123123)
    # loop over frames from the output stream
    if isRight:
        while True:
            # wait until the lock is acquired
            with lock:
                # check if the output frame is available, otherwise skip
                # the iteration of the loop
                if outputFrame is None:
                    continue

                # encode the frame in JPEG format
                (flag, encodedImage) = cv2.imencode(".jpg", outputFrame)

                # ensure the frame was successfully encoded
                if not flag:
                    continue

            # yield the output frame in the byte format
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' +
                   bytearray(encodedImage) + b'\r\n')
    else:
        while True:
            # wait until the lock is acquired
            with lock:
                # check if the output frame is available, otherwise skip
                # the iteration of the loop
                if outputFrame_right is None:
                    continue

                # encode the frame in JPEG format
                (flag, encodedImage) = cv2.imencode(".jpg", outputFrame_right)

                # ensure the frame was successfully encoded
                if not flag:
                    continue

            # yield the output frame in the byte format
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' +
                   bytearray(encodedImage) + b'\r\n')


@app.route("/video_feed")
def video_feed():
    # return the response generated along with the specific media
    # type (mime type)
    print('video_feed')
    return Response(generate(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/video_feed_right")
def video_feed_right():
    # return the response generated along with the specific media
    # type (mime type)
    print('video_feed_right')
    return Response(generate(isRight=True),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


# check to see if this is the main thread of execution
if __name__ == '__main__':
    # construct the argument parser and parse command line arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--ip", type=str, default='0.0.0.0',
                    help="ip address of the device")
    ap.add_argument("-o", "--port", type=int, default='8000',
                    help="ephemeral port number of the server (1024 to 65535)")
    ap.add_argument("-f", "--frame-count", type=int, default=0,
                    help="# of frames used to construct the background model")
    args = vars(ap.parse_args())
    print('threading before')
    # start a thread that will perform motion detection
    # detect_motion(frameCount=args["frame_count"])
    t = threading.Thread(target=detect_motion, args=(
        args["frame_count"],))
    t.daemon = True
    t.start()
    print('start!!!')

    # start the flask app
    app.run(host=args["ip"], port=args["port"], debug=True,
            threaded=True, use_reloader=False)
cap.stop()