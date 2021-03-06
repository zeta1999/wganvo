# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Trains and Evaluates the MNIST network using a feed dictionary."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# pylint: disable=missing-docstring
import argparse
import time
# Scipy
from scipy import linalg

# from ... import transform
import sys, os, inspect
import random
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

# currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
# parentdir = os.path.dirname(currentdir)
# sys.path.insert(0, parentdir)
# import sys
# from os import path
# sys.path.append( path.dirname( path.dirname( path.abspath(__file__) ) ) )

import tensorflow as tf
import numpy as np
import numpy.matlib as matlib
import math

# from transform import se3_to_components
# import transformations
# Basic model parameters as external flags.
FLAGS = None
DEFAULT_INTRINSIC_FILE_NAME = "intrinsic_matrix.txt"


def placeholder_inputs(batch_size, images_placeholder_name=None, targets_placeholder_name=None):
    """Generate placeholder variables to represent the input tensors.
    These placeholders are used as inputs by the rest of the model building
    code and will be fed from the downloaded data in the .run() loop, below.
    Args:
            batch_size: The batch size will be baked into both placeholders.
    Returns:
            images_placeholder: Images placeholder.
            labels_placeholder: Labels placeholder.
    """
    # Note that the shapes of the placeholders match the shapes of the full
    # image and label tensors, except the first dimension is now batch_size
    # rather than the full size of the train or test data sets.
    images_placeholder = tf.placeholder(tf.float32, name=images_placeholder_name, shape=(batch_size,
                                                                                         input_data.IMAGE_HEIGHT,
                                                                                         input_data.IMAGE_WIDTH, 2))
    labels_placeholder = tf.placeholder(tf.float32, name=targets_placeholder_name,
                                        shape=(batch_size, input_data.LABELS_SIZE))
    return images_placeholder, labels_placeholder


def fill_feed_dict(data_set, images_pl, labels_pl, feed_with_batch=False, batch_size=None, shuffle=True,
                   standardize_targets=False, fake_data=False):
    """Fills the feed_dict for training the given step or for evaluating the entire dataset.
    A feed_dict takes the form of:
    feed_dict = {
        <placeholder>: <tensor of values to be passed for placeholder>,
        ....
    }
    Args:
      data_set: The set of images and labels, from input_data.read_data_sets()
      images_pl: The images placeholder, from placeholder_inputs().
      labels_pl: The labels placeholder, from placeholder_inputs().
    Returns:
      feed_dict: The feed dictionary mapping from placeholders to values.
    """
    # Create the feed_dict for the placeholders filled with the next
    # `batch size` examples.
    assert False  # Deprecated Usar el de eval_utils
    if (feed_with_batch):
        if (batch_size is None):
            raise ValueError("batch_size not specified")
        images_feed, labels_feed = data_set.next_batch(batch_size,
                                                       fake_data,
                                                       shuffle=shuffle,
                                                       standardize_targets=standardize_targets)
    # Create the feed_dict for the placeholders filled with the entire dataset
    else:
        images_feed = data_set.images
        labels_feed = data_set.labels

    feed_dict = {
        images_pl: images_feed,
        labels_pl: labels_feed,
    }
    return feed_dict


def do_evaluation(sess,
                  outputs,
                  images_placeholder,
                  labels_placeholder,
                  data_set,
                  batch_size,
                  # k_matrix,
                  standardize_targets,
                  train_mode=None):
    # target_variance_vector):
    """Runs one evaluation against the full epoch of data.
    Args:
        sess: The session in which the model has been trained.
        evaluation: .
        images_placeholder: The images placeholder.
        labels_placeholder: The labels placeholder.
        data_set: The set of images and labels to evaluate, from
        input_data.read_data_sets().
    """
    import transformations
    import eval_utils
    rows_reshape = 3
    columns_reshape = 4
    components_vector_size = 6
    evaluation = tf.square(tf.subtract(outputs, labels_placeholder))
    # batch_size = FLAGS.batch_size
    steps_per_epoch = data_set.num_examples // batch_size
    num_examples = steps_per_epoch * batch_size
    # prediction_matrix = np.empty((num_examples, components_vector_size), dtype="float32")
    target_matrix = np.empty((num_examples, components_vector_size), dtype="float32")
    # accum_squared_errors = np.zeros((batch_size, input_data.LABELS_SIZE), dtype="float32")
    squared_errors = np.zeros(components_vector_size, dtype="float32")
    accum_geod_distance = 0.
    for step in xrange(steps_per_epoch):
        feed_dict = eval_utils.fill_feed_dict(data_set,
                                              images_placeholder,
                                              labels_placeholder,
                                              feed_with_batch=True,
                                              shuffle=False,
                                              batch_size=batch_size,
                                              standardize_targets=standardize_targets)
        if train_mode is not None:
            feed_dict[train_mode] = False
        prediction, target = sess.run([outputs, labels_placeholder], feed_dict=feed_dict)
        if standardize_targets:  # if true, convert back to original scale
            prediction = prediction * data_set.targets_std + data_set.targets_mean
            target = target * data_set.targets_std + data_set.targets_mean
        # accum_squared_errors += batch_squared_errors
        init = step * batch_size
        end = (step + 1) * batch_size
        # prediction_matrix[init:end] = prediction
        # target_matrix[init:end] = target
        for i in xrange(batch_size):
            assert init + i < end
            index = init + i
            # current_prediction = prediction[i].reshape(rows_reshape, columns_reshape)
            # P = K * [R|t] => [R|t] = K^(-1) * P
            # curr_pred_transform_matrix = inv_k_matrix * current_prediction
            # current_target = target[i].reshape(rows_reshape, columns_reshape)
            # curr_target_transform_matrix = inv_k_matrix * current_target
            # Get the closest rotation matrix
            # u, _ = linalg.polar(curr_pred_transform_matrix[0:3, 0:3])
            # Replace the non-orthogonal R matrix obtained from the prediction with the closest rotation matrix
            # closest_curr_pred_s3_matrix = matlib.identity(4)
            # closest_curr_pred_s3_matrix[0:3, 0:3] = u
            # closest_curr_pred_s3_matrix[0:3, 3] = curr_pred_transform_matrix[0:3, 3]
            # curr_target_s3_matrix = np.concatenate([curr_target_transform_matrix, [[0, 0, 0, 1]]], axis=0)
            # From [R|t] matrix to components
            # components = [x,y,z, roll, pitch, yaw]
            # curr_pred_components = se3_to_components(closest_curr_pred_s3_matrix)
            # curr_target_components = se3_to_components(curr_target_s3_matrix)

            current_prediction = prediction[i]
            prediction_quaternion = current_prediction[3:7]
            euler = transformations.euler_from_quaternion(prediction_quaternion)
            curr_pred_components = np.hstack((current_prediction[0:3], euler))
            current_target = target[i]
            target_quaternion = current_target[3:7]
            euler = transformations.euler_from_quaternion(target_quaternion)
            curr_target_components = np.hstack((current_target[0:3], euler))

            dot = np.dot(target_quaternion, prediction_quaternion)
            geod_distance = 2 * acos(np.abs(dot))
            accum_geod_distance += geod_distance
            curr_squared_error = np.square(curr_pred_components - curr_target_components)
            squared_errors += curr_squared_error
            # prediction_matrix[index] = curr_pred_components
            target_matrix[index] = curr_target_components

    print("---------------------------------------------------------")
    print("Prediction")
    print(current_prediction)
    # print("Prediction (closest [R|t])")
    # print(closest_curr_pred_s3_matrix)
    print("Target")
    print(current_target)
    mean_squared_errors = squared_errors / num_examples
    rmse_x = np.sqrt(np.sum(squared_errors[0:3]) / num_examples)
    rmse_ang = np.sqrt(np.sum(squared_errors[3:6]) / num_examples)
    mean_geod_dist_q = accum_geod_distance / num_examples
    target_variance = np.var(target_matrix, axis=0)  # variance = std ** 2
    norm_mse = mean_squared_errors / target_variance
    return rmse_x, rmse_ang, mean_geod_dist_q, mean_squared_errors, norm_mse


# def frames_vs_abs_distance(relative_poses_prediction, relative_poses_target):
#     absolute_poses_prediction = eval_utils.get_absolute_poses(relative_poses_prediction).reshape(-1, 12)
#     absolute_poses_target = eval_utils.get_absolute_poses(relative_poses_target).reshape(-1, 12)
#     poses_prediction = se3_pose_list(absolute_poses_prediction)
#     poses_target = se3_pose_list(absolute_poses_target)
#     poses_prediction = trajectory.PosePath3D(poses_se3=poses_prediction)
#     poses_target = trajectory.PosePath3D(poses_se3=poses_target)
#
#     E_tr = poses_prediction.positions_xyz - poses_target.positions_xyz
#     traslation_error = [np.linalg.norm(E_i) for E_i in E_tr]
#     traslation_error = np.array(traslation_error)
#
#     max_num_of_points = len(absolute_poses_prediction)
#     samples = min(50, max_num_of_points)
#     X = random.sample(range(max_num_of_points), samples)
#     Y = traslation_error[X]
#
#     return X, Y
# E_rot = [ape_base(x_t, x_t_star) for x_t, x_t_star in
#         zip(poses_prediction.poses_se3, poses_target.poses_se3)]
# rotation_error = np.array(
#    [np.linalg.norm(
#        lie_algebra.so3_from_se3(E_i) - np.eye(3)) for E_i in E_rot])
# return rmse(traslation_error), rmse(rotation_error)



def rmse(error):
    squared_errors = np.power(error, 2)
    return math.sqrt(np.mean(squared_errors))


def ape_base(x_t, x_t_star):
    """
    Computes the absolute error pose for a single SE(3) pose pair
    following the notation of the Kummerle paper.
    :param x_t: estimated absolute pose at t
    :param x_t_star: reference absolute pose at t
    .:return: the delta pose
    """
    return lie_algebra.relative_se3(x_t, x_t_star)


def acos(x):
    res = np.arccos(x)
    if math.isnan(res):
        return (-0.69813170079773212 * x * x - 0.87266462599716477) * x + 1.5707963267948966
    return res


def add_array_to_tensorboard(arr, prefix_tagname, summary_writer, step):
    ind = 1
    summary = tf.Summary()
    for std in arr:
        tagname = prefix_tagname + str(ind)
        summary.value.add(tag=tagname, simple_value=std)
        ind += 1
    summary_writer.add_summary(summary, step)
    summary_writer.flush()


def add_scalar_to_tensorboard(value, tagname, summary_writer, step):
    summary = tf.Summary()
    summary.value.add(tag=tagname, simple_value=value)
    summary_writer.add_summary(summary, step)


def run_training():
    print("START")
    # se3_to_components(np.array([1,2,3]))
    """Train MNIST for a number of steps."""
    # Get the sets of images and labels for training, validation, and
    # test on MNIST.
    kfold = 5
    train_images, train_targets, splits, train_groups, train_points = input_data.read_data_sets(FLAGS.train_data_dir,
                                                                                                kfold)
    test_images, test_targets, _, test_groups, _ = input_data.read_data_sets(FLAGS.test_data_dir)

    # intrinsic_matrix = np.matrix(load(FLAGS.intrinsics_dir))
    # if FLAGS.test_intrinsics_dir:
    #     test_intrinsic_matrix = np.matrix(load(FLAGS.test_intrinsics_dir))
    # else:
    #     test_intrinsic_matrix = intrinsic_matrix
    # Tell TensorFlow that the model will be built into the default Graph.
    print("Learning rate: " + str(FLAGS.learning_rate))
    print("Steps: " + str(FLAGS.max_steps))
    print("Batch size: " + str(FLAGS.batch_size))
    print(FLAGS)
    with tf.Graph().as_default():
        # Generate placeholders for the images and labels.
        images_placeholder, labels_placeholder = placeholder_inputs(
            FLAGS.batch_size, images_placeholder_name="images_placeholder",
            targets_placeholder_name="targets_placeholder")
        points = tf.placeholder(tf.float32,
                                shape=[FLAGS.batch_size, 3, input_data.IMAGE_POINTS],
                                name="train_points_placeholder")
        # train_dataset_images_placeholder, train_dataset_labels_placeholder = placeholder_inputs(
        #    data_sets.train.num_examples)
        train_mode = tf.placeholder(tf.bool, name="train_mode")

        # Build a Graph that computes predictions from the inference model.
        outputs = model.inference(images_placeholder, train_mode, FLAGS.pruned_vgg, FLAGS.pooling, FLAGS.act_function)

        # Rename
        outputs = tf.identity(outputs, name="outputs")
        # train_targets_variance = np.var(data_sets.train.labels, axis=0)
        # (X- np.mean(X, axis=0)) / np.std(X,axis=0) #Guardar la media y el std para volver a los valores originales

        standardize_targets = False
        # Add to the Graph the Ops for loss calculation.
        # sx = tf.Variable(0., name="regression_sx")
        # sq = tf.Variable(-3., name="regression_sq")
        loss = model.kendall_loss_naive(outputs, labels_placeholder)  # model.loss(outputs, labels_placeholder)

        # Add to the Graph the Ops that calculate and apply gradients.
        train_op = model.training(loss, FLAGS.learning_rate)

        # Add the Op to compare the logits to the labels during evaluation.
        # evaluation = model.evaluation(outputs, labels_placeholder)

        # Build the summary Tensor based on the TF collection of Summaries.
        summary = tf.summary.merge_all()

        # Add the variable initializer Op.
        init = tf.global_variables_initializer()

        # Create a session for running Ops on the Graph.
        sess = tf.Session()

        # Instantiate a SummaryWriter to output summaries and the Graph.
        # summary_writer = tf.summary.FileWriter(FLAGS.log_dir, sess.graph)

        test_dataset = input_data.DataSet(test_images, test_targets, test_groups, fake_data=FLAGS.fake_data)
        # And then after everything is built:

        current_fold = 0
        # FIXME ver como loguear
        require_improvement = 12000
        total_start_time = time.time()
        for train_indexs, validation_indexs in splits:
            # Create a saver for writing training checkpoints.
            saver = tf.train.Saver(max_to_keep=1)
            our_metric_saver = tf.train.Saver(max_to_keep=1)
            current_fold += 1
            best_validation_performance = 1000000.
            our_metric_test_performance = 1000000.
            print("**************** NEW FOLD *******************")
            print("Train size: " + str(len(train_indexs)))
            print("Validation size: " + str(len(validation_indexs)))
            train_dataset = input_data.DataSet(train_images[train_indexs], train_targets[train_indexs],
                                               fake_data=FLAGS.fake_data, points=train_points)
            fwriter_str = "fold_" + str(current_fold)
            curr_fold_log_path = os.path.join(FLAGS.log_dir, fwriter_str)
            # Instantiate a SummaryWriter to output summaries and the Graph.
            summary_writer = tf.summary.FileWriter(curr_fold_log_path, sess.graph)
            last_improvement = 0
            our_metric_last_improvement = 0
            # Run the Op to initialize the variables.
            sess.run(init)
            # Start the training loop.
            for step in xrange(FLAGS.max_steps):
                start_time = time.time()
                # Fill a feed dictionary with the actual set of images and labels
                # for this particular training step.
                feed_dict = eval_utils.fill_feed_dict(train_dataset,
                                                      images_placeholder,
                                                      labels_placeholder,
                                                      points_pl=points,
                                                      feed_with_batch=True,
                                                      batch_size=FLAGS.batch_size,
                                                      standardize_targets=standardize_targets)
                feed_dict[train_mode] = True

                # Run one step of the model.  The return values are the activations
                # from the `train_op` (which is discarded) and the `loss` Op.  To
                # inspect the values of your Ops or variables, you may include them
                # in the list passed to sess.run() and the value tensors will be
                # returned in the tuple from the call.
                _, loss_value = sess.run([train_op, loss],
                                         feed_dict=feed_dict)

                # Write the summaries and print an overview fairly often.
                if step % 100 == 0:
                    duration = time.time() - start_time
                    # Print status to stdout.
                    print('Step %d: loss = %.2f (%.3f sec)' % (step, loss_value, duration))
                    # Update the events file.
                    summary_str = sess.run(summary, feed_dict=feed_dict)
                    summary_writer.add_summary(summary_str, step)
                    summary_writer.flush()
                # Save a checkpoint and evaluate the model periodically.
                if (step + 1) % 1000 == 0 or (step + 1) == FLAGS.max_steps:
                    # Evaluate against the training set.

                    # print('Training Data Eval:')
                    # train_rmse_x, train_rmse_ang, train_dist_q, train_mse, train_norm_mse = do_evaluation(sess,
                    #                                                                      outputs,
                    #                                                                      images_placeholder,
                    #                                                                      labels_placeholder,
                    #                                                                      train_dataset,
                    #                                                                      FLAGS.batch_size,
                    #                                                                      standardize_targets)
                    # add_scalar_to_tensorboard(train_rmse_x, "tr_rmse_x", summary_writer, step)
                    # add_scalar_to_tensorboard(train_rmse_ang, "tr_rmse_ang", summary_writer, step)
                    # add_scalar_to_tensorboard(train_dist_q, "tr_gdist_q", summary_writer, step)
                    # add_array_to_tensorboard(train_mse, "tr_mse_", summary_writer, step)
                    # add_array_to_tensorboard(train_norm_mse, "tr_norm_mse_", summary_writer, step)
                    # Evaluate against the validation set.
                    print('Validation Data Eval:')
                    validation_rmse_x, validation_rmse_ang, validation_dist_q, validation_mse, validation_norm_mse = do_evaluation(
                        sess,
                        outputs,
                        images_placeholder,
                        labels_placeholder,
                        input_data.DataSet(
                            train_images[
                                validation_indexs],
                            train_targets[
                                validation_indexs],
                            train_groups[
                                validation_indexs],
                            fake_data=FLAGS.fake_data),
                        FLAGS.batch_size,
                        # intrinsic_matrix,
                        standardize_targets,
                        train_mode)
                    add_scalar_to_tensorboard(validation_rmse_x, "v_rmse_x", summary_writer, step)
                    add_scalar_to_tensorboard(validation_rmse_ang, "v_rmse_ang", summary_writer, step)
                    add_scalar_to_tensorboard(validation_dist_q, "v_gdist_q", summary_writer, step)
                    add_array_to_tensorboard(validation_mse, "v_mse_", summary_writer, step)
                    add_array_to_tensorboard(validation_norm_mse, "v_norm_mse_", summary_writer, step)
                    # Evaluate against the test set.
                    print('Test Data Eval:')
                    test_rmse_x, test_rmse_ang, test_dist_q, test_mse, test_norm_mse = do_evaluation(sess,
                                                                                      outputs,
                                                                                      images_placeholder,
                                                                                      labels_placeholder,
                                                                                      test_dataset,
                                                                                      FLAGS.batch_size,
                                                                                      # test_intrinsic_matrix,
                                                                                      standardize_targets,
                                                                                      train_mode)
                    add_scalar_to_tensorboard(test_rmse_x, "te_rmse_x", summary_writer, step)
                    add_scalar_to_tensorboard(test_rmse_ang, "te_rmse_ang", summary_writer, step)
                    add_scalar_to_tensorboard(test_dist_q, "te_gdist_q", summary_writer, step)
                    add_array_to_tensorboard(test_mse, "te_mse_", summary_writer, step)
                    add_array_to_tensorboard(test_norm_mse, "te_norm_mse_", summary_writer, step)

                    test_dataset.reset_epoch()

                    print("Test Eval:")
                    relative_prediction, relative_target = eval_utils.infer_relative_poses(sess, test_dataset,
                                                                                           FLAGS.batch_size,
                                                                                           images_placeholder, outputs,
                                                                                           labels_placeholder,
                                                                                           train_mode)
                    save_txt = step == 999 or step == 19999 or step == 39999
                    te_eval = eval_utils.our_metric_evaluation(relative_prediction, relative_target, test_dataset,
                                                               curr_fold_log_path, save_txt)
                    print(te_eval)
                    add_scalar_to_tensorboard(te_eval, "mean(square(log(d)/log(f)))", summary_writer, step)
                    # add_scalar_to_tensorboard(mean_ape_rmse_tr, "test_mean_ape_rmse_tr", summary_writer, step)
                    # add_scalar_to_tensorboard(mean_ape_rmse_rot, "test_mean_ape_rmse_rot", summary_writer, step)

                    # Keep the best model
                    v_eval = (validation_rmse_x + 100 * validation_dist_q) / 2
                    add_scalar_to_tensorboard(v_eval, "v_eval", summary_writer, step)

                    if te_eval < our_metric_test_performance:
                        our_metric_test_performance = te_eval
                        our_metric_last_improvement = step
                        checkpoint_file = os.path.join(curr_fold_log_path, 'our-metric-vgg-model')
                        our_metric_saver.save(sess, checkpoint_file, global_step=step)

                    if v_eval < best_validation_performance:
                        best_validation_performance = v_eval
                        last_improvement = step
                        checkpoint_file = os.path.join(curr_fold_log_path, 'vgg-model')
                        saver.save(sess, checkpoint_file, global_step=step)
                    if step - last_improvement > require_improvement:
                        print("No improvement found in a while, stopping optimization. Last improvement = step %d" % (
                            last_improvement))
                        break
        total_duration = time.time() - total_start_time
        print('Total: %.3f sec' % (total_duration))


def main(_):
    # if tf.gfile.Exists(FLAGS.log_dir):

    # tf.gfile.DeleteRecursively(FLAGS.log_dir)
    # tf.gfile.MakeDirs(FLAGS.log_dir)
    run_training()


if __name__ == '__main__':
    currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
    parentdir = os.path.dirname(currentdir)
    sys.path.insert(0, parentdir)

    import input_data
    import model
    import eval_utils
    import trajectory
    import lie_algebra
    from array_utils import load

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

    # parser.add_argument(
    #    '--validation_data_dir',
    #    type=str,
    #    help='Directory to put the test data.'
    # )

    parser.add_argument(
        '--learning_rate',
        type=float,
        default=0.01,
        help='Initial learning rate.'
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
    # parser.add_argument(
    #     '--intrinsics_dir',
    #     type=str,
    #     default=os.path.join(os.getcwd(), DEFAULT_INTRINSIC_FILE_NAME),
    #     help='Intrinsic matrix path'
    # )
    # parser.add_argument(
    #     '--test_intrinsics_dir',
    #     type=str,
    #     help='Intrinsic matrix path'
    # )
    parser.add_argument(
        '--fake_data',
        default=False,
        help='If true, uses fake data for unit testing.',
        action='store_true'
    )
    parser.add_argument('--pruned_vgg',
                        action='store_true',
                        help='Train a small cnn')
    parser.add_argument('--pooling',
                        choices=["avg", "max"],
                        default="max",
                        help='Pooling')
    parser.add_argument('--act_function',
                        choices=["relu", "leaky_relu"],
                        default="relu",
                        help='Activation function')

    FLAGS, unparsed = parser.parse_known_args()
    tf.app.run(main=main, argv=[sys.argv[0]] + unparsed)
