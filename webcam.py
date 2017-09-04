from __future__ import print_function, division

import os
import argparse
import cv2
import numpy as np
import tensorflow as tf
from utils import preserve_colors
from utils import get_files, get_img, get_img_crop
from utils import WebcamVideoStream, FPS
from scipy.ndimage.filters import gaussian_filter
from inference import AdaINference


parser = argparse.ArgumentParser()
parser.add_argument('-src', '--source', dest='video_source', type=int,
                    default=0, help='Device index of the camera.')
parser.add_argument('--checkpoint', type=str, help='Checkpoint directory', required=True)
parser.add_argument('--style-path', type=str, dest='style_path', help='Style images folder', required=True)
parser.add_argument('--width', type=int, help='Webcam video width', default=None)
parser.add_argument('--height', type=int, help='Webcam video height', default=None)
parser.add_argument('--video-out', type=str, help="Save to output video file if not None", default=None)
parser.add_argument('--fps', type=int, help="Frames Per Second for output video file", default=10)
parser.add_argument('--scale', type=float, help="Scale the output image", default=1)
parser.add_argument('--keep-colors', action='store_true', help="Preserve the colors of the style image", default=False)
parser.add_argument('--device', type=str,
                        dest='device', help='Device to perform compute on',
                        default='/gpu:0')
parser.add_argument('--style-size', type=int, help="Resize style image to this size before cropping 256x256", default=512)
parser.add_argument('--alpha', type=float, help="Alpha blend value", default=1)
parser.add_argument('--concat', action='store_true', help="Concatenate style image and stylized output", default=False)
parser.add_argument('--interpolate', action='store_true', help="Interpolate between two images", default=False)
parser.add_argument('--noise', action='store_true', help="Synthesize textures from noise images", default=False)
parser.add_argument('-r', '--random', type=int, help='Load a random img every # iterations', default=0)
args = parser.parse_args()


class StyleWindow(object):
    '''Helper class to handle style image settings'''

    def __init__(self, style_path, img_size=512, scale=1, alpha=1, interpolate=False):
        self.style_imgs = get_files(style_path)

        self.style_rgbs = [None, None]

        self.img_size = img_size
        self.crop_size = 256
        self.scale = scale
        self.alpha = alpha

        cv2.namedWindow('Style Controls')
        if len(self.style_imgs) > 1:
            # Select style image by index
            cv2.createTrackbar('index','Style Controls', 0, len(self.style_imgs)-1, self.set_idx)
        
        # Blend param for AdaIN transform
        cv2.createTrackbar('alpha','Style Controls', 100, 100, self.set_alpha)

        # Resize style to this size before cropping
        cv2.createTrackbar('size','Style Controls', img_size, 2048, self.set_size)

        # Size of square crop box for style
        cv2.createTrackbar('crop size','Style Controls', 256, 2048, self.set_crop_size)

        # Scale the content before processing
        cv2.createTrackbar('scale','Style Controls', int(scale*100), 200, self.set_scale)

        self.set_style(random=True, window='Style Controls', style_idx=0)

        if interpolate:
            # Create a window to show second style image for interpolation
            cv2.namedWindow('style2')
            self.interp_weight = 1.
            cv2.createTrackbar('interpolation','Style Controls', 100, 100, self.set_interp)
            self.set_style(random=True, style_idx=1, window='style2')

    def set_style(self, idx=None, random=False, style_idx=0, window='Style Controls'):
        if idx is not None:
            self.idx = idx
        if random:
            self.idx = np.random.randint(len(self.style_imgs))

        style_file = self.style_imgs[self.idx]
        self.style_rgbs[style_idx] = get_img_crop(style_file, resize=self.img_size, crop=self.crop_size)
        self.show_style(window, self.style_rgbs[style_idx])

    def set_idx(self, idx):
        self.set_style(idx)

    def set_size(self, size):
        self.img_size = max(size, self.crop_size)      # Don't go below crop_size
        self.set_style()

    def set_crop_size(self, crop_size):
        self.crop_size = min(crop_size, self.img_size) # Don't go above img_size
        self.set_style()

    def set_scale(self, scale):
        self.scale = scale / 100
        
    def set_alpha(self, alpha):
        self.alpha = alpha / 100

    def show_style(self, window, style_rgb):
        cv2.imshow(window, cv2.cvtColor(cv2.resize(style_rgb, (args.style_size, args.style_size)), cv2.COLOR_RGB2BGR))

    def set_interp(self, weight):
        self.interp_weight = weight / 100


def main():
    # Load the AdaIN model
    ada_in = AdaINference(args.checkpoint, device=args.device)

    # Load a panel to control style settings
    style_window = StyleWindow(args.style_path, args.style_size, args.scale, args.alpha, args.interpolate)

    # Start the webcam stream
    cap = WebcamVideoStream(args.video_source, args.width, args.height).start()

    _, frame = cap.read()

    # Grab a sample frame to calculate frame size
    frame_resize = cv2.resize(frame, None, fx=args.scale, fy=args.scale)
    img_shape = frame_resize.shape

    # Setup video out writer
    if args.video_out is not None:
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        if args.concat:
            out_shape = (img_shape[1]+img_shape[0],img_shape[0]) # Make room for the style img
        else:
            out_shape = (img_shape[1],img_shape[0])
        print('Video Out Shape:', out_shape)
        video_writer = cv2.VideoWriter(args.video_out, fourcc, args.fps, out_shape)
    
    fps = FPS().start() # Track FPS processing speed

    keep_colors = args.keep_colors

    count = 0

    while(True):
        ret, frame = cap.read()

        if ret is True:       
            frame_resize = cv2.resize(frame, None, fx=style_window.scale, fy=style_window.scale)

            if args.noise:  # Generate textures from noise instead of images
                frame_resize = np.random.randint(0, 256, frame_resize.shape, np.uint8)
                frame_resize = gaussian_filter(frame_resize, sigma=0.5)

            count += 1
            print("Frame:",count,"Orig shape:",frame.shape,"New shape",frame_resize.shape)

            image_rgb = cv2.cvtColor(frame_resize, cv2.COLOR_BGR2RGB)  # OpenCV uses BGR, we need RGB

            if args.random > 0 and count % args.random == 0:
                style_window.set_style(random=True, style_idx=0)

            if keep_colors:
                style_window.style_rgb = preserve_colors(style_window.style_rgb, content_img)

            if args.interpolate is False:
                # Run the frame through the style network
                stylized_rgb = ada_in.predict(image_rgb, style_window.style_rgbs[0], style_window.alpha)
            else:
                interp_weights = [style_window.interp_weight, 1 - style_window.interp_weight]
                stylized_rgb = ada_in.predict_interpolate(image_rgb, 
                                                          style_window.style_rgbs,
                                                          interp_weights,
                                                          style_window.alpha)

            # Stitch the style + stylized output together, but only if there's one style image
            if args.concat and args.interpolate is False:
                # Resize style img to same height as frame
                style_rgb_resized = cv2.resize(style_window.style_rgbs[0], (stylized_rgb.shape[0], stylized_rgb.shape[0]))
                stylized_rgb = np.hstack([style_rgb_resized, stylized_rgb])
            
            stylized_bgr = cv2.cvtColor(stylized_rgb, cv2.COLOR_RGB2BGR)
                
            if args.video_out is not None:
                stylized_bgr = cv2.resize(stylized_bgr, out_shape) # Make sure frame matches video size
                video_writer.write(stylized_bgr)

            cv2.imshow('AdaIN Style', stylized_bgr)

            fps.update()

            key = cv2.waitKey(10) 
            if key & 0xFF == ord('r'):   # Load new random style
                style_window.set_style(random=True, style_idx=0)
                if args.interpolate:     # Load a a second style if interpolating
                    style_window.set_style(random=True, style_idx=1, window='style2')    
            elif key & 0xFF == ord('c'):
                keep_colors = not keep_colors
                print("Switching to keep_colors",keep_colors)
            elif key & 0xFF == ord('q'): # Quit
                break
        else:
            break

    fps.stop()
    print('[INFO] elapsed time (total): {:.2f}'.format(fps.elapsed()))
    print('[INFO] approx. FPS: {:.2f}'.format(fps.fps()))

    cap.stop()
    
    if args.video_out is not None:
        video_writer.release()
    
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
