import adapt_images
import os
import argparse
import numpy as np
from image import non_demosaic_load, savez_compressed
from transform import build_intrinsic_matrix
import matplotlib.pyplot as plt
from array_utils import list_to_array, save
#from transformations import euler_from_matrix, translation_from_matrix


_CAM2INDEX = {'cam0': 0, 'cam1': 1,'cam2': 2, 'cam3': 3}
_CAM2FOLDER = {'cam0': 'image_0', 'cam1': 'image_1','cam2': 'image_2','cam3': 'image_3'}


def get_arguments():
    parser = argparse.ArgumentParser(description='Play back images from a given directory')
    parser.add_argument('dir', type=str, help='Directory containing iamge sequence')
    parser.add_argument('poses_file', type=str, help='File containing absolute poses')
    #parser.add_argument('calib_file', type=str, help='File containing calibration parameters')
    parser.add_argument("--cam", help="One of the four cameras",
                             default="cam0", choices=["cam0", "cam1", "cam2", "cam3"])
    parser.add_argument('--crop', nargs=2, default=None, type=int, metavar=('WIDTH', 'HEIGHT'),
                        help='(optional) If supplied, images will be cropped to WIDTH x HEIGHT. It is the new resolution after cropping.')
    parser.add_argument('--scale', nargs=2, default=None, type=int, metavar=('WIDTH', 'HEIGHT'),
                        help='(optional) If supplied, images will be scaled to WIDTH x HEIGHT')
    parser.add_argument('--output_dir', type=str, default=None, help='(optional) Output directory')
    # parser.add_argument('image_name', type=str, help='Image name.')
    args = parser.parse_args()
    return args


def to_homogeneous(m):
    identity = np.asmatrix(np.identity(4))
    identity[0:3, :] = m
    return identity


def vector_to_homogeneous(v):
    m = np.asmatrix(v.reshape(3,4))
    return to_homogeneous(m)


def get_intrinsics_parameters(focal_length, principal_point, resolution, crop=None, scale=None):
    if crop: # focal_length remains the same. We must adjust the principal_point according to the new resolution
        principal_point = [principal_point[i] - (resolution[i] - crop[i]) / 2. for i in range(len(principal_point))]
        resolution = crop
    if scale:
        focal_length = [float(scale[i])/resolution[i] * focal_length[i] for i in range(len(focal_length))]
        principal_point = [float(scale[i])/resolution[i] * principal_point[i] for i in range(len(principal_point))]
    return focal_length, principal_point


def show(images):
    artist = plt.imshow(images[0], cmap='gray')
    for img in images:
        artist.set_data(img)
        plt.xticks([])
        plt.yticks([])
        plt.pause(0.01)


def get_calibration_matrix(calibration, cam):
    idx = _CAM2INDEX[cam]
    calibration_matrix = calibration[idx,1:]
    return calibration_matrix.reshape(3,4)


def get_image_folder_name(cam):
    return _CAM2FOLDER[cam]


def calculate_transformation(pose_a, pose_b):
    return np.linalg.inv(pose_a) * pose_b


def main():
    args = get_arguments()
    DEFAULT_CALIBRATION_FILENAME = 'calib.txt'
    if args.cam != "cam0":
        raise Exception("Only cam0 is supported")
    output_dir = os.curdir
    if args.output_dir:
        output_dir = args.output_dir
    if not os.path.isdir(output_dir):
        raise IOError(output_dir + "is not an existing folder")
    image_folder_name = get_image_folder_name(args.cam)
    image_dir = os.path.join(args.dir, image_folder_name)
    calib_file = os.path.join(args.dir, DEFAULT_CALIBRATION_FILENAME)
    images_filenames = sorted([item for item in os.listdir(image_dir) if os.path.isfile(os.path.join(image_dir, item))])
    images_list = []
    for img_name in images_filenames:
        img = non_demosaic_load(os.path.join(image_dir,img_name))
        original_resolution = adapt_images.get_resolution(img)
        assert isinstance(img, np.ndarray) and img.dtype == np.uint8 and img.flags.contiguous
        modified_img, _ = adapt_images.process_image(img, crop=args.crop, scale=args.scale)
        assert isinstance(modified_img, np.ndarray) and modified_img.dtype == np.uint8 and modified_img.flags.contiguous
        images_list.append(modified_img)
    print(original_resolution)
    compressed_images = list_to_array(images_list)
    save(os.path.join(output_dir, 'images_shape'), compressed_images.shape, fmt='%i')
    print compressed_images.shape
    compressed_images_path = os.path.join(output_dir, 'images')
    savez_compressed(compressed_images_path, compressed_images)

    t_records = []
    p_records = []
    offset = 1
    with open(args.poses_file) as poses_file, open(calib_file) as calibration_file:
        calib = np.genfromtxt(calibration_file, delimiter=' ')
        calibration_matrix = get_calibration_matrix(calib, args.cam)
        print(calibration_matrix.reshape(-1))
        new_focal_length, new_principal_point = get_intrinsics_parameters([calibration_matrix[0,0], calibration_matrix[1,1]], [calibration_matrix[0,2],calibration_matrix[1,2]],original_resolution,crop=args.crop, scale=args.scale)
        new_intrinsic_matrix = build_intrinsic_matrix(new_focal_length, new_principal_point)
        poses = np.loadtxt(poses_file, delimiter=' ')
        assert len(images_filenames) == len(poses)
        for idx_pose in xrange(poses.shape[0] - offset):
            dst_index = idx_pose
            src_index = idx_pose + offset
            src_pose = poses[src_index]
            dst_pose = poses[dst_index]
            src = vector_to_homogeneous(src_pose)
            dst = vector_to_homogeneous(dst_pose)
            transf_src_dst = calculate_transformation(src, dst)
            t_matrix = transf_src_dst[0:3, :]
            p_matrix = new_intrinsic_matrix * t_matrix
            t_records.append((t_matrix, src_index, dst_index))
            p_records.append((p_matrix, src_index, dst_index))
            #aux.append(np.asarray(t_matrix).reshape(-1))
    transf = np.array(t_records, dtype=[('T', ('float32', (3, 4))), ('src_idx', 'int32'), ('dst_idx', 'int32')])
    proy = np.array(p_records, dtype=[('P', ('float32', (3, 4))), ('src_idx', 'int32'), ('dst_idx', 'int32')])
    savez_compressed(os.path.join(output_dir, 't'), transf)
    savez_compressed(os.path.join(output_dir, 'p'), proy)
    save(os.path.join(output_dir, "intrinsic_matrix"), new_intrinsic_matrix, fmt='%.18e')
    save(os.path.join(output_dir, "intrinsic_parameters"), [new_focal_length, new_principal_point], fmt='%.18e')
    #ts = list_to_array(aux)
    #print(euler_from_matrix(transf_src_dst))
    #print(translation_from_matrix(transf_src_dst))
    #save('/home/jcremona/mybo', ts, fmt='%.18e')

if __name__ == "__main__":
    main()