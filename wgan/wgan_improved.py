import os, sys, inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)
# sys.path.append(os.pardir)
# sys.path.append(os.get)
import time
import functools

import numpy as np
import tensorflow as tf
import sklearn.datasets

import tflib as lib
import tflib.ops.linear
import tflib.ops.conv2d
import tflib.ops.batchnorm
import tflib.ops.deconv2d
import tflib.save_images
import tflib.small_imagenet
import tflib.ops.layernorm
import tflib.plot

from vgg_trainable.input_data import read_data_sets, DataSet
from vgg_trainable.main import fill_feed_dict, add_scalar_to_tensorboard, add_array_to_tensorboard, do_evaluation
from array_utils import load

# Download 64x64 ImageNet at http://image-net.org/small/download.php and
# fill in the path to the extracted files here!
# DATA_DIR = ''
# if len(DATA_DIR) == 0:
#    raise Exception('Please specify path to data directory in gan_64x64.py!')

MODE = 'wgan-gp'  # dcgan, wgan, wgan-gp, lsgan
DIM = 64  # Model dimensionality
CRITIC_ITERS = 5  # How many iterations to train the critic for
N_GPUS = 1  # Number of GPUs
# BATCH_SIZE = 64  # Batch size. Must be a multiple of N_GPUS
# ITERS = 200000  # How many iterations to train for
LAMBDA = 10  # Gradient penalty lambda hyperparameter
OUTPUT_DIM = None  # 64 * 64 * 3  # Number of pixels in each iamge

lib.print_model_settings(locals().copy())


def GeneratorAndDiscriminator():
    """
    Choose which generator and discriminator architecture to use by
    uncommenting one of these lines.
    """

    return DCGANGenerator, DCGANDiscriminator

    # For actually generating decent samples, use this one
    # return GoodGenerator, GoodDiscriminator

    # Baseline (G: DCGAN, D: DCGAN)
    # return DCGANGenerator, DCGANDiscriminator

    # No BN and constant number of filts in G
    # return WGANPaper_CrippledDCGANGenerator, DCGANDiscriminator

    # 512-dim 4-layer ReLU MLP G
    # return FCGenerator, DCGANDiscriminator

    # No normalization anywhere
    # return functools.partial(DCGANGenerator, bn=False), functools.partial(DCGANDiscriminator, bn=False)

    # Gated multiplicative nonlinearities everywhere
    # return MultiplicativeDCGANGenerator, MultiplicativeDCGANDiscriminator

    # tanh nonlinearities everywhere
    # return functools.partial(DCGANGenerator, bn=True, nonlinearity=tf.tanh), \
    #        functools.partial(DCGANDiscriminator, bn=True, nonlinearity=tf.tanh)

    # 101-layer ResNet G and D
    # return ResnetGenerator, ResnetDiscriminator

    raise Exception('You must choose an architecture!')


DEVICES = ['/gpu:{}'.format(i) for i in xrange(N_GPUS)]


def LeakyReLU(x, alpha=0.2):
    return tf.maximum(alpha * x, x)


def ReLULayer(name, n_in, n_out, inputs):
    output = lib.ops.linear.Linear(name + '.Linear', n_in, n_out, inputs, initialization='he')
    return tf.nn.relu(output)


def LeakyReLULayer(name, n_in, n_out, inputs):
    output = lib.ops.linear.Linear(name + '.Linear', n_in, n_out, inputs, initialization='he')
    return LeakyReLU(output)


def Normalize(name, axes, inputs):
    if ('Discriminator' in name) and (MODE == 'wgan-gp'):
        if axes != [0, 2, 3]:
            raise Exception('Layernorm over non-standard axes is unsupported')
        return lib.ops.layernorm.Layernorm(name, [1, 2, 3], inputs)
    else:
        return lib.ops.batchnorm.Batchnorm(name, axes, inputs, fused=True)


def pixcnn_gated_nonlinearity(a, b):
    return tf.sigmoid(a) * tf.tanh(b)


def SubpixelConv2D(*args, **kwargs):
    kwargs['output_dim'] = 4 * kwargs['output_dim']
    output = lib.ops.conv2d.Conv2D(*args, **kwargs)
    output = tf.transpose(output, [0, 2, 3, 1])
    output = tf.depth_to_space(output, 2)
    output = tf.transpose(output, [0, 3, 1, 2])
    return output


def ConvMeanPool(name, input_dim, output_dim, filter_size, inputs, he_init=True, biases=True):
    output = lib.ops.conv2d.Conv2D(name, input_dim, output_dim, filter_size, inputs, he_init=he_init, biases=biases)
    output = tf.add_n(
        [output[:, :, ::2, ::2], output[:, :, 1::2, ::2], output[:, :, ::2, 1::2], output[:, :, 1::2, 1::2]]) / 4.
    return output


def MeanPoolConv(name, input_dim, output_dim, filter_size, inputs, he_init=True, biases=True):
    output = inputs
    output = tf.add_n(
        [output[:, :, ::2, ::2], output[:, :, 1::2, ::2], output[:, :, ::2, 1::2], output[:, :, 1::2, 1::2]]) / 4.
    output = lib.ops.conv2d.Conv2D(name, input_dim, output_dim, filter_size, output, he_init=he_init, biases=biases)
    return output


def UpsampleConv(name, input_dim, output_dim, filter_size, inputs, he_init=True, biases=True):
    output = inputs
    output = tf.concat([output, output, output, output], axis=1)
    output = tf.transpose(output, [0, 2, 3, 1])
    output = tf.depth_to_space(output, 2)
    output = tf.transpose(output, [0, 3, 1, 2])
    output = lib.ops.conv2d.Conv2D(name, input_dim, output_dim, filter_size, output, he_init=he_init, biases=biases)
    return output


def BottleneckResidualBlock(name, input_dim, output_dim, filter_size, inputs, resample=None, he_init=True):
    """
    resample: None, 'down', or 'up'
    """
    if resample == 'down':
        conv_shortcut = functools.partial(lib.ops.conv2d.Conv2D, stride=2)
        conv_1 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=input_dim / 2)
        conv_1b = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim / 2, output_dim=output_dim / 2, stride=2)
        conv_2 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=output_dim / 2, output_dim=output_dim)
    elif resample == 'up':
        conv_shortcut = SubpixelConv2D
        conv_1 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=input_dim / 2)
        conv_1b = functools.partial(lib.ops.deconv2d.Deconv2D, input_dim=input_dim / 2, output_dim=output_dim / 2)
        conv_2 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=output_dim / 2, output_dim=output_dim)
    elif resample == None:
        conv_shortcut = lib.ops.conv2d.Conv2D
        conv_1 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=input_dim / 2)
        conv_1b = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim / 2, output_dim=output_dim / 2)
        conv_2 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim / 2, output_dim=output_dim)

    else:
        raise Exception('invalid resample value')

    if output_dim == input_dim and resample == None:
        shortcut = inputs  # Identity skip-connection
    else:
        shortcut = conv_shortcut(name + '.Shortcut', input_dim=input_dim, output_dim=output_dim, filter_size=1,
                                 he_init=False, biases=True, inputs=inputs)

    output = inputs
    output = tf.nn.relu(output)
    output = conv_1(name + '.Conv1', filter_size=1, inputs=output, he_init=he_init)
    output = tf.nn.relu(output)
    output = conv_1b(name + '.Conv1B', filter_size=filter_size, inputs=output, he_init=he_init)
    output = tf.nn.relu(output)
    output = conv_2(name + '.Conv2', filter_size=1, inputs=output, he_init=he_init, biases=False)
    output = Normalize(name + '.BN', [0, 2, 3], output)

    return shortcut + (0.3 * output)


def ResidualBlock(name, input_dim, output_dim, filter_size, inputs, resample=None, he_init=True):
    """
    resample: None, 'down', or 'up'
    """
    if resample == 'down':
        conv_shortcut = MeanPoolConv
        conv_1 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=input_dim)
        conv_2 = functools.partial(ConvMeanPool, input_dim=input_dim, output_dim=output_dim)
    elif resample == 'up':
        conv_shortcut = UpsampleConv
        conv_1 = functools.partial(UpsampleConv, input_dim=input_dim, output_dim=output_dim)
        conv_2 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=output_dim, output_dim=output_dim)
    elif resample == None:
        conv_shortcut = lib.ops.conv2d.Conv2D
        conv_1 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=input_dim)
        conv_2 = functools.partial(lib.ops.conv2d.Conv2D, input_dim=input_dim, output_dim=output_dim)
    else:
        raise Exception('invalid resample value')

    if output_dim == input_dim and resample == None:
        shortcut = inputs  # Identity skip-connection
    else:
        shortcut = conv_shortcut(name + '.Shortcut', input_dim=input_dim, output_dim=output_dim, filter_size=1,
                                 he_init=False, biases=True, inputs=inputs)

    output = inputs
    output = Normalize(name + '.BN1', [0, 2, 3], output)
    output = tf.nn.relu(output)
    output = conv_1(name + '.Conv1', filter_size=filter_size, inputs=output, he_init=he_init, biases=False)
    output = Normalize(name + '.BN2', [0, 2, 3], output)
    output = tf.nn.relu(output)
    output = conv_2(name + '.Conv2', filter_size=filter_size, inputs=output, he_init=he_init)

    return shortcut + output


# ! Generators

def GoodGenerator(n_samples, noise=None, dim=DIM, nonlinearity=tf.nn.relu):
    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = lib.ops.linear.Linear('Generator.Input', 128, 4 * 4 * 8 * dim, noise)
    output = tf.reshape(output, [-1, 8 * dim, 4, 4])

    output = ResidualBlock('Generator.Res1', 8 * dim, 8 * dim, 3, output, resample='up')
    output = ResidualBlock('Generator.Res2', 8 * dim, 4 * dim, 3, output, resample='up')
    output = ResidualBlock('Generator.Res3', 4 * dim, 2 * dim, 3, output, resample='up')
    output = ResidualBlock('Generator.Res4', 2 * dim, 1 * dim, 3, output, resample='up')

    output = Normalize('Generator.OutputN', [0, 2, 3], output)
    output = tf.nn.relu(output)
    output = lib.ops.conv2d.Conv2D('Generator.Output', 1 * dim, 3, 3, output)
    output = tf.tanh(output)

    return tf.reshape(output, [-1, OUTPUT_DIM])


def FCGenerator(n_samples, noise=None, FC_DIM=512):
    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = ReLULayer('Generator.1', 128, FC_DIM, noise)
    output = ReLULayer('Generator.2', FC_DIM, FC_DIM, output)
    output = ReLULayer('Generator.3', FC_DIM, FC_DIM, output)
    output = ReLULayer('Generator.4', FC_DIM, FC_DIM, output)
    output = lib.ops.linear.Linear('Generator.Out', FC_DIM, OUTPUT_DIM, output)

    output = tf.tanh(output)

    return output


def DCGANGenerator(n_samples, noise=None, dim=DIM, bn=True, nonlinearity=tf.nn.relu):
    lib.ops.conv2d.set_weights_stdev(0.02)
    lib.ops.deconv2d.set_weights_stdev(0.02)
    lib.ops.linear.set_weights_stdev(0.02)

    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = lib.ops.linear.Linear('Generator.Input', 128, 4 * 4 * 8 * dim, noise)
    output = tf.reshape(output, [-1, 8 * dim, 4, 4])
    if bn:
        output = Normalize('Generator.BN1', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.2', 8 * dim, 4 * dim, 5, output)
    if bn:
        output = Normalize('Generator.BN2', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.3', 4 * dim, 2 * dim, 5, output)
    if bn:
        output = Normalize('Generator.BN3', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.4', 2 * dim, dim, 5, output)
    if bn:
        output = Normalize('Generator.BN4', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.5', dim, 6, 5, output)
    output = tf.tanh(output)

    lib.ops.conv2d.unset_weights_stdev()
    lib.ops.deconv2d.unset_weights_stdev()
    lib.ops.linear.unset_weights_stdev()

    return tf.reshape(output, [-1, 2 * 128 * 96])


def WGANPaper_CrippledDCGANGenerator(n_samples, noise=None, dim=DIM):
    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = lib.ops.linear.Linear('Generator.Input', 128, 4 * 4 * dim, noise)
    output = tf.nn.relu(output)
    output = tf.reshape(output, [-1, dim, 4, 4])

    output = lib.ops.deconv2d.Deconv2D('Generator.2', dim, dim, 5, output)
    output = tf.nn.relu(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.3', dim, dim, 5, output)
    output = tf.nn.relu(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.4', dim, dim, 5, output)
    output = tf.nn.relu(output)

    output = lib.ops.deconv2d.Deconv2D('Generator.5', dim, 3, 5, output)
    output = tf.tanh(output)

    return tf.reshape(output, [-1, OUTPUT_DIM])


def ResnetGenerator(n_samples, noise=None, dim=DIM):
    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = lib.ops.linear.Linear('Generator.Input', 128, 4 * 4 * 8 * dim, noise)
    output = tf.reshape(output, [-1, 8 * dim, 4, 4])

    for i in xrange(6):
        output = BottleneckResidualBlock('Generator.4x4_{}'.format(i), 8 * dim, 8 * dim, 3, output, resample=None)
    output = BottleneckResidualBlock('Generator.Up1', 8 * dim, 4 * dim, 3, output, resample='up')
    for i in xrange(6):
        output = BottleneckResidualBlock('Generator.8x8_{}'.format(i), 4 * dim, 4 * dim, 3, output, resample=None)
    output = BottleneckResidualBlock('Generator.Up2', 4 * dim, 2 * dim, 3, output, resample='up')
    for i in xrange(6):
        output = BottleneckResidualBlock('Generator.16x16_{}'.format(i), 2 * dim, 2 * dim, 3, output, resample=None)
    output = BottleneckResidualBlock('Generator.Up3', 2 * dim, 1 * dim, 3, output, resample='up')
    for i in xrange(6):
        output = BottleneckResidualBlock('Generator.32x32_{}'.format(i), 1 * dim, 1 * dim, 3, output, resample=None)
    output = BottleneckResidualBlock('Generator.Up4', 1 * dim, dim / 2, 3, output, resample='up')
    for i in xrange(5):
        output = BottleneckResidualBlock('Generator.64x64_{}'.format(i), dim / 2, dim / 2, 3, output, resample=None)

    output = lib.ops.conv2d.Conv2D('Generator.Out', dim / 2, 3, 1, output, he_init=False)
    output = tf.tanh(output / 5.)

    return tf.reshape(output, [-1, OUTPUT_DIM])


def MultiplicativeDCGANGenerator(n_samples, noise=None, dim=DIM, bn=True):
    if noise is None:
        noise = tf.random_normal([n_samples, 128])

    output = lib.ops.linear.Linear('Generator.Input', 128, 4 * 4 * 8 * dim * 2, noise)
    output = tf.reshape(output, [-1, 8 * dim * 2, 4, 4])
    if bn:
        output = Normalize('Generator.BN1', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.deconv2d.Deconv2D('Generator.2', 8 * dim, 4 * dim * 2, 5, output)
    if bn:
        output = Normalize('Generator.BN2', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.deconv2d.Deconv2D('Generator.3', 4 * dim, 2 * dim * 2, 5, output)
    if bn:
        output = Normalize('Generator.BN3', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.deconv2d.Deconv2D('Generator.4', 2 * dim, dim * 2, 5, output)
    if bn:
        output = Normalize('Generator.BN4', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.deconv2d.Deconv2D('Generator.5', dim, 3, 5, output)
    output = tf.tanh(output)

    return tf.reshape(output, [-1, OUTPUT_DIM])


# ! Discriminators

def GoodDiscriminator(inputs, dim=DIM):
    output = tf.reshape(inputs, [-1, 3, 64, 64])
    output = lib.ops.conv2d.Conv2D('Discriminator.Input', 3, dim, 3, output, he_init=False)

    output = ResidualBlock('Discriminator.Res1', dim, 2 * dim, 3, output, resample='down')
    output = ResidualBlock('Discriminator.Res2', 2 * dim, 4 * dim, 3, output, resample='down')
    output = ResidualBlock('Discriminator.Res3', 4 * dim, 8 * dim, 3, output, resample='down')
    output = ResidualBlock('Discriminator.Res4', 8 * dim, 8 * dim, 3, output, resample='down')

    output = tf.reshape(output, [-1, 4 * 4 * 8 * dim])
    output = lib.ops.linear.Linear('Discriminator.Output', 4 * 4 * 8 * dim, 1, output)

    return tf.reshape(output, [-1])


def MultiplicativeDCGANDiscriminator(inputs, dim=DIM, bn=True):
    output = tf.reshape(inputs, [-1, 3, 64, 64])

    output = lib.ops.conv2d.Conv2D('Discriminator.1', 3, dim * 2, 5, output, stride=2)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.conv2d.Conv2D('Discriminator.2', dim, 2 * dim * 2, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN2', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.conv2d.Conv2D('Discriminator.3', 2 * dim, 4 * dim * 2, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN3', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = lib.ops.conv2d.Conv2D('Discriminator.4', 4 * dim, 8 * dim * 2, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN4', [0, 2, 3], output)
    output = pixcnn_gated_nonlinearity(output[:, ::2], output[:, 1::2])

    output = tf.reshape(output, [-1, 4 * 4 * 8 * dim])
    output = lib.ops.linear.Linear('Discriminator.Output', 4 * 4 * 8 * dim, 1, output)

    return tf.reshape(output, [-1])


def ResnetDiscriminator(inputs, dim=DIM):
    output = tf.reshape(inputs, [-1, 3, 64, 64])
    output = lib.ops.conv2d.Conv2D('Discriminator.In', 3, dim / 2, 1, output, he_init=False)

    for i in xrange(5):
        output = BottleneckResidualBlock('Discriminator.64x64_{}'.format(i), dim / 2, dim / 2, 3, output, resample=None)
    output = BottleneckResidualBlock('Discriminator.Down1', dim / 2, dim * 1, 3, output, resample='down')
    for i in xrange(6):
        output = BottleneckResidualBlock('Discriminator.32x32_{}'.format(i), dim * 1, dim * 1, 3, output, resample=None)
    output = BottleneckResidualBlock('Discriminator.Down2', dim * 1, dim * 2, 3, output, resample='down')
    for i in xrange(6):
        output = BottleneckResidualBlock('Discriminator.16x16_{}'.format(i), dim * 2, dim * 2, 3, output, resample=None)
    output = BottleneckResidualBlock('Discriminator.Down3', dim * 2, dim * 4, 3, output, resample='down')
    for i in xrange(6):
        output = BottleneckResidualBlock('Discriminator.8x8_{}'.format(i), dim * 4, dim * 4, 3, output, resample=None)
    output = BottleneckResidualBlock('Discriminator.Down4', dim * 4, dim * 8, 3, output, resample='down')
    for i in xrange(6):
        output = BottleneckResidualBlock('Discriminator.4x4_{}'.format(i), dim * 8, dim * 8, 3, output, resample=None)

    output = tf.reshape(output, [-1, 4 * 4 * 8 * dim])
    output = lib.ops.linear.Linear('Discriminator.Output', 4 * 4 * 8 * dim, 1, output)

    return tf.reshape(output / 5., [-1])


def FCDiscriminator(inputs, FC_DIM=512, n_layers=3):
    output = LeakyReLULayer('Discriminator.Input', OUTPUT_DIM, FC_DIM, inputs)
    for i in xrange(n_layers):
        output = LeakyReLULayer('Discriminator.{}'.format(i), FC_DIM, FC_DIM, output)
    output = lib.ops.linear.Linear('Discriminator.Out', FC_DIM, 1, output)

    return tf.reshape(output, [-1])


def DCGANDiscriminator(inputs, dim=DIM, bn=True, nonlinearity=LeakyReLU):
    output = tf.reshape(inputs, [-1, 2, 96, 128])
    lib.ops.conv2d.set_weights_stdev(0.02)
    lib.ops.deconv2d.set_weights_stdev(0.02)
    lib.ops.linear.set_weights_stdev(0.02)

    output = lib.ops.conv2d.Conv2D('Discriminator.1', 2, dim, 5, output, stride=2)
    output = nonlinearity(output)

    output = lib.ops.conv2d.Conv2D('Discriminator.2', dim, 2 * dim, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN2', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.conv2d.Conv2D('Discriminator.3', 2 * dim, 4 * dim, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN3', [0, 2, 3], output)
    output = nonlinearity(output)

    output = lib.ops.conv2d.Conv2D('Discriminator.4', 4 * dim, 8 * dim, 5, output, stride=2)
    if bn:
        output = Normalize('Discriminator.BN4', [0, 2, 3], output)
    output = nonlinearity(output)

    output1 = tf.reshape(output, [-1, 4 * 4 * 8 * dim])
    output2 = tf.reshape(output, [-1, 96 * 128 * 2])
    output_disc = lib.ops.linear.Linear('Discriminator.Output', 4 * 4 * 8 * dim, 1, output1)
    output_vo = lib.ops.linear.Linear('Discriminator.Output.VO', 96 * 128 * 2, 12, output2)
    lib.ops.conv2d.unset_weights_stdev()
    lib.ops.deconv2d.unset_weights_stdev()
    lib.ops.linear.unset_weights_stdev()

    return tf.reshape(output_disc, [-1]), output_vo


Generator, Discriminator = GeneratorAndDiscriminator()


def vo_cost_function(outputs, targets):
    return tf.reduce_mean(tf.abs(tf.subtract(outputs, targets)))


def run(args):
    with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as session:

        all_real_data_conv = tf.placeholder(tf.float32, shape=[args.batch_size, 96, 128, 2])
        vo_targets = tf.placeholder(tf.float32, shape=[args.batch_size, 12])

        real_data = tf.reshape(all_real_data_conv, [args.batch_size,
                                                    96 * 128 * 2])  # tf.reshape(2 * ((tf.cast(real_data_conv, tf.float32) / 255.) - .5),
        #           [args.batch_size / len(DEVICES), OUTPUT_DIM])
        fake_data = Generator(args.batch_size)
        disc_real, disc_real_vo = Discriminator(real_data)
        disc_fake, _ = Discriminator(fake_data)

        if MODE == 'wgan':
            gen_cost = -tf.reduce_mean(disc_fake)
            disc_cost = tf.reduce_mean(disc_fake) - tf.reduce_mean(disc_real)

        elif MODE == 'wgan-gp':
            gen_cost = -tf.reduce_mean(disc_fake)
            disc_cost = tf.reduce_mean(disc_fake) - tf.reduce_mean(disc_real)
            disc_vo_cost = vo_cost_function(disc_real_vo, vo_targets)
            alpha = tf.random_uniform(
                shape=[args.batch_size, 1],
                minval=0.,
                maxval=1.
            )
            differences = fake_data - real_data
            interpolates = real_data + (alpha * differences)
            gradients = tf.gradients(Discriminator(interpolates), [interpolates])[0]
            slopes = tf.sqrt(tf.reduce_sum(tf.square(gradients), reduction_indices=[1]))
            gradient_penalty = tf.reduce_mean((slopes - 1.) ** 2)
            disc_cost += LAMBDA * gradient_penalty

        elif MODE == 'dcgan':
            try:  # tf pre-1.0 (bottom) vs 1.0 (top)
                gen_cost = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=disc_fake,
                                                                                  labels=tf.ones_like(
                                                                                      disc_fake)))
                disc_cost = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=disc_fake,
                                                                                   labels=tf.zeros_like(
                                                                                       disc_fake)))
                disc_cost += tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=disc_real,
                                                                                    labels=tf.ones_like(
                                                                                        disc_real)))
            except Exception as e:
                gen_cost = tf.reduce_mean(
                    tf.nn.sigmoid_cross_entropy_with_logits(disc_fake, tf.ones_like(disc_fake)))
                disc_cost = tf.reduce_mean(
                    tf.nn.sigmoid_cross_entropy_with_logits(disc_fake, tf.zeros_like(disc_fake)))
                disc_cost += tf.reduce_mean(
                    tf.nn.sigmoid_cross_entropy_with_logits(disc_real, tf.ones_like(disc_real)))
            disc_cost /= 2.

        elif MODE == 'lsgan':
            gen_cost = tf.reduce_mean((disc_fake - 1) ** 2)
            disc_cost = (tf.reduce_mean((disc_real - 1) ** 2) + tf.reduce_mean((disc_fake - 0) ** 2)) / 2.

        else:
            raise Exception()

        if MODE == 'wgan':
            gen_train_op = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(gen_cost,
                                                                                  var_list=lib.params_with_name(
                                                                                      'Generator'),
                                                                                  colocate_gradients_with_ops=True)
            disc_train_op = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(disc_cost,
                                                                                   var_list=lib.params_with_name(
                                                                                       'Discriminator.'),
                                                                                   colocate_gradients_with_ops=True)

            clip_ops = []
            for var in lib.params_with_name('Discriminator'):
                clip_bounds = [-.01, .01]
                clip_ops.append(tf.assign(var, tf.clip_by_value(var, clip_bounds[0], clip_bounds[1])))
            clip_disc_weights = tf.group(*clip_ops)

        elif MODE == 'wgan-gp':
            # con var_list le indicamos los parametros (pesos) que queremos actualizar, en el primer caso los del Generator, en el segundo caso los del Disc
            gen_train_op = tf.train.AdamOptimizer(learning_rate=1e-4, beta1=0., beta2=0.9).minimize(gen_cost,
                                                                                                    var_list=lib.params_with_name(
                                                                                                        'Generator'),
                                                                                                    colocate_gradients_with_ops=True)
            disc_train_op = tf.train.AdamOptimizer(learning_rate=1e-4, beta1=0., beta2=0.9).minimize(disc_cost,
                                                                                                     var_list=lib.params_with_name(
                                                                                                         'Discriminator.'),
                                                                                                     colocate_gradients_with_ops=True)
            disc_vo_train_op = tf.train.AdamOptimizer(learning_rate=1e-4, beta1=0., beta2=0.9).minimize(disc_vo_cost,
                                                                                                        var_list=lib.params_with_name(
                                                                                                            'Discriminator.'),
                                                                                                        colocate_gradients_with_ops=True)

        elif MODE == 'dcgan':
            gen_train_op = tf.train.AdamOptimizer(learning_rate=2e-4, beta1=0.5).minimize(gen_cost,
                                                                                          var_list=lib.params_with_name(
                                                                                              'Generator'),
                                                                                          colocate_gradients_with_ops=True)
            disc_train_op = tf.train.AdamOptimizer(learning_rate=2e-4, beta1=0.5).minimize(disc_cost,
                                                                                           var_list=lib.params_with_name(
                                                                                               'Discriminator.'),
                                                                                           colocate_gradients_with_ops=True)

        elif MODE == 'lsgan':
            gen_train_op = tf.train.RMSPropOptimizer(learning_rate=1e-4).minimize(gen_cost,
                                                                                  var_list=lib.params_with_name(
                                                                                      'Generator'),
                                                                                  colocate_gradients_with_ops=True)
            disc_train_op = tf.train.RMSPropOptimizer(learning_rate=1e-4).minimize(disc_cost,
                                                                                   var_list=lib.params_with_name(
                                                                                       'Discriminator.'),
                                                                                   colocate_gradients_with_ops=True)

        else:
            raise Exception()

        # For generating samples
        fixed_noise = tf.constant(np.random.normal(size=(args.batch_size, 128)).astype('float32'))
        all_fixed_noise_samples = []
        n_samples = args.batch_size
        all_fixed_noise_samples.append(
            Generator(n_samples, noise=fixed_noise[0: n_samples]))
        if tf.__version__.startswith('1.'):
            all_fixed_noise_samples = tf.concat(all_fixed_noise_samples, axis=0)
        else:
            all_fixed_noise_samples = tf.concat(0, all_fixed_noise_samples)

        def generate_image(iteration):
            samples = session.run(all_fixed_noise_samples)
            samples = ((samples + 1.) * (255.99 / 2)).astype('int32')
            # FIXME resolucion
            # lib.save_images.save_images(samples.reshape((args.batch_size, 3, 64, 64)), 'samples_{}.png'.format(iteration))

        # Dataset iterator
        # train_gen, dev_gen = lib.small_imagenet.load(args.batch_size, data_dir=DATA_DIR)

        # def inf_train_gen():
        #    while True:
        #        for (images,) in train_gen():
        #            yield images

        # Save a batch of ground-truth samples
        # _x = inf_train_gen().next()
        # _x_r = session.run(real_data, feed_dict={real_data_conv: _x[:args.batch_size/N_GPUS]})
        # _x_r = ((_x_r+1.)*(255.99/2)).astype('int32')
        # lib.save_images.save_images(_x_r.reshape((args.batch_size/N_GPUS, 3, 64, 64)), 'samples_groundtruth.png') TODO por ahora no


        kfold = 5
        train_images, train_targets, splits = read_data_sets(args.train_data_dir, kfold)
        test_images, test_targets, _ = read_data_sets(args.test_data_dir)
        intrinsic_matrix = np.matrix(load(args.intrinsics_file))
        if args.test_intrinsics_file:
            test_intrinsic_matrix = np.matrix(load(args.test_intrinsics_file))
        else:
            test_intrinsic_matrix = intrinsic_matrix

        test_dataset = DataSet(test_images, test_targets)
        # Add the variable initializer Op.
        init = tf.global_variables_initializer()
        standardize_targets = True
        current_fold = 0
        require_improvement = 12000
        # Train loop
        for train_indexs, validation_indexs in splits:

            print("**************** NEW FOLD *******************")
            print("Train size: " + str(len(train_indexs)))
            print("Validation size: " + str(len(validation_indexs)))
            train_dataset = DataSet(train_images[train_indexs], train_targets[train_indexs])
            saver = tf.train.Saver()
            current_fold += 1
            fwriter_str = "fold_" + str(current_fold)
            curr_fold_log_path = os.path.join(args.log_dir, fwriter_str)
            # Instantiate a SummaryWriter to output summaries and the Graph.
            summary_writer = tf.summary.FileWriter(curr_fold_log_path, session.graph)
            best_validation_performance = 1000000.
            session.run(init)

            # gen = inf_train_gen()
            for iteration in xrange(args.max_steps):

                start_time = time.time()

                # Train generator
                if iteration > 0:
                    _ = session.run(gen_train_op)

                # Train critic and VO
                if (MODE == 'dcgan') or (MODE == 'lsgan'):
                    disc_iters = 1
                else:
                    disc_iters = CRITIC_ITERS
                for i in xrange(disc_iters):
                    feed_dict = fill_feed_dict(train_dataset,
                                               all_real_data_conv,
                                               vo_targets,
                                               True,
                                               batch_size=args.batch_size,
                                               standardize_targets=standardize_targets)
                    # _data = gen.next()
                    _disc_cost, _disc_vo_cost, _, _ = session.run(
                        [disc_cost, disc_vo_cost, disc_train_op, disc_vo_train_op], feed_dict=feed_dict)

                    if MODE == 'wgan':
                        _ = session.run([clip_disc_weights])

                lib.plot.plot('train disc cost', _disc_cost)
                lib.plot.plot('train vo cost', _disc_vo_cost)
                lib.plot.plot('time', time.time() - start_time)

                if iteration % 200 == 199:
                    t = time.time()
                    dev_disc_costs = []
                    # for (images,) in dev_gen():
                    #    _dev_disc_cost = session.run(disc_cost, feed_dict={all_real_data_conv: images})
                    #    dev_disc_costs.append(_dev_disc_cost)
                    # lib.plot.plot('dev disc cost', np.mean(dev_disc_costs))

                    # generate_image(iteration) TODO Por ahora no

                if (iteration < 5) or (iteration % 200 == 199):
                    lib.plot.flush(args.log_dir)
                # Save a checkpoint and evaluate the model periodically.
                if (iteration + 1) % 1000 == 0 or (iteration + 1) == FLAGS.max_steps:
                    # Evaluate against the training set.
                    print('Training Data Eval:')
                    train_rmse, train_mse, train_norm_mse = do_evaluation(session,
                                                                          disc_real_vo,
                                                                          all_real_data_conv,
                                                                          vo_targets,
                                                                          train_dataset,
                                                                          args.batch_size,
                                                                          intrinsic_matrix,
                                                                          standardize_targets)
                    add_scalar_to_tensorboard(train_rmse, "tr_rmse", summary_writer, iteration)
                    add_array_to_tensorboard(train_mse, "tr_mse_", summary_writer, iteration)
                    add_array_to_tensorboard(train_norm_mse, "tr_norm_mse_", summary_writer, iteration)
                    # Evaluate against the validation set.
                    print('Validation Data Eval:')
                    validation_rmse, validation_mse, validation_norm_mse = do_evaluation(session,
                                                                                         disc_real_vo,
                                                                                         all_real_data_conv,
                                                                                         vo_targets,
                                                                                         DataSet(
                                                                                             train_images[
                                                                                                 validation_indexs],
                                                                                             train_targets[
                                                                                                 validation_indexs]),
                                                                                         args.batch_size,
                                                                                         intrinsic_matrix,
                                                                                         standardize_targets)
                    add_scalar_to_tensorboard(validation_rmse, "v_rmse", summary_writer, iteration)
                    add_array_to_tensorboard(validation_mse, "v_mse_", summary_writer, iteration)
                    add_array_to_tensorboard(validation_norm_mse, "v_norm_mse_", summary_writer, iteration)
                    # Evaluate against the test set.
                    print('Test Data Eval:')
                    test_rmse, test_mse, test_norm_mse = do_evaluation(session,
                                                                       disc_real_vo,
                                                                       all_real_data_conv,
                                                                       vo_targets,
                                                                       test_dataset,
                                                                       args.batch_size,
                                                                       test_intrinsic_matrix,
                                                                       standardize_targets)
                    add_scalar_to_tensorboard(test_rmse, "te_rmse", summary_writer, iteration)
                    add_array_to_tensorboard(test_mse, "te_mse_", summary_writer, iteration)
                    add_array_to_tensorboard(test_norm_mse, "te_norm_mse_", summary_writer, iteration)
                    # Keep the best model
                    if validation_rmse < best_validation_performance:
                        best_validation_performance = validation_rmse
                        last_improvement = iteration
                        checkpoint_file = os.path.join(curr_fold_log_path, 'wgan-model')
                        saver.save(session, checkpoint_file, global_step=iteration)
                    if iteration - last_improvement > require_improvement:
                        print(
                            "No improvement found in a while, stopping optimization. Last improvement = step %d" % (
                                last_improvement))
                        break
                lib.plot.tick()


def main(_):
    run(FLAGS)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        'train_data_dir',
        type=str,
        default=".",
        help='Directory to put the train data.'
    )
    parser.add_argument(
        'test_data_dir',
        type=str,
        default=".",
        help='Directory to put the test data.'
    )
    parser.add_argument(
        '--max_steps',
        type=int,
        default=10000,
        help='Number of steps to run trainer.'
    )
    parser.add_argument(
        '--batch_size',
        type=int,
        default=100,
        help='Batch size.  Must divide evenly into the dataset sizes.'
    )
    parser.add_argument(
        '--log_dir',
        type=str,
        default=os.path.join(os.getenv('TEST_TMPDIR', '/tmp'),
                             'tensorflow/jcremona/tesina/logs/'),
        help='Directory to put the log data.'
    )
    parser.add_argument(
        '--intrinsics_file',
        type=str,
        default=os.path.join(os.getcwd(), "intrinsic_matrix.txt"),
        help='Intrinsic matrix path'
    )
    parser.add_argument(
        '--test_intrinsics_file',
        type=str,
        help='Intrinsic matrix path'
    )
    FLAGS, unparsed = parser.parse_known_args()
    tf.app.run(main=main, argv=[sys.argv[0]] + unparsed)
