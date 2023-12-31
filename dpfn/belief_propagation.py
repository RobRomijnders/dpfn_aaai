"""Belief propagation for CRISP-like models."""
import numpy as np
from dpfn import constants, util, util_bp
import numba
import time
from typing import Optional, Tuple


# @numba.njit((
#   'float32[:, :, :](float32[:, :], float64, float32[:, :], '
#   'int64, float64, float64, float64, float64)'))
@numba.njit
def adjust_matrices_map(
    A_matrix: np.ndarray,
    p1: float,
    forward_messages: np.ndarray,
    num_time_steps: int,
    clip_lower: float,
    clip_upper: float,
    epsilon_dp: float,
    a_rdp: float) -> np.ndarray:
  """Adjusts dynamics matrices based in messages from incoming contacts."""
  A_adjusted = np.copy(A_matrix)
  A_adjusted = np.ones((num_time_steps, 4, 4), dtype=np.float32) * A_adjusted

  # First collate all incoming forward messages according to timestep
  log_probs = np.ones(
    (num_time_steps), dtype=np.float32) * np.log(A_matrix[0][0])
  for row in forward_messages:
    _, user_me, timestep, p_inf_message = (
      int(row[0]), int(row[1]), int(row[2]), row[3])
    if user_me < 0:
      break
    # assert user_me == user, f"User {user_me} is not {user}"

    # Calculation
    if epsilon_dp > 0:
      # Add for numerical stability, otherwise BP belief could be NaN
      p_inf_message = np.minimum(np.maximum(p_inf_message, 0.01), 0.99)
    add_term = np.log(1 - p1*p_inf_message)
    log_probs[timestep] += add_term

  if a_rdp > 0:
    clip_upper = np.minimum(clip_upper, 0.99999)
    clip_lower = np.maximum(clip_lower, 0.00001)
    sensitivity = np.abs(np.log(1-clip_upper*p1) - np.log(1-clip_lower*p1))

    num_contacts = np.sum(forward_messages[:, 0] >= 0)
    log_probs = util.add_lognormal_noise_rdp(
      log_probs, a_rdp, epsilon_dp, sensitivity)

    # Everything hereafter is post-processing
    # Clip to [0, 1], equals clip to [\infty, 0] in logdomain
    known_upper = num_contacts*np.log(1-clip_lower*p1)
    known_lower = num_contacts*np.log(1-clip_upper*p1)

    log_probs = np.minimum(
      log_probs, known_upper).astype(np.float32)
    log_probs = np.maximum(
      log_probs, known_lower).astype(np.float32)

  transition_prob = np.exp(log_probs)

  if epsilon_dp > 0:
    transition_prob = np.minimum(
      np.maximum(transition_prob, np.float32(0.00001)), np.float32(0.99999))

  A_adjusted[:, 0, 0] = transition_prob
  A_adjusted[:, 0, 1] = 1. - transition_prob

  return A_adjusted


# @numba.njit(
#   ('UniTuple(float32[:, :], 3)('
#    'float32[:, :], float64, float64, int64, float32[:, :], float32[:, :], '
#    'int64, float32[:, :], float64, float64, float64, float64)'))
@numba.njit
def forward_backward_user(
    A_matrix: np.ndarray,
    p0: float,
    p1: float,
    user: int,
    backward_messages: np.ndarray,
    forward_messages: np.ndarray,
    num_time_steps: int,
    obs_messages: np.ndarray,
    clip_lower: float,
    clip_upper: float,
    epsilon_dp: float,
    a_rdp: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
  """Does forward backward step for one user.

  Args:
    user: integer which is the user index (in absolute counting!!)
    map_backward_mesage: for each user, this is an array of size [CTC, 7]
      where the 7 columns are
      * user from
      * user to
      * timestep of contact
      * backward message S
      * backward message E
      * backward message I
      * backward message R
    map_backward_mesage: for each user, this is an array of size [CTC, 4]
      where the 4 columns are
      * user from
      * user to
      * timestep of contact
      * forward message as scalar

  Returns:
    marginal beliefs for this user after running bp, and the forward and
    backward messages that this user sends out.
  """
  mu_back_contact = (
    np.ones((num_time_steps, 4), dtype=np.float32) * np.float32(.25))
  if epsilon_dp < 0:  # Only calculate backward messages in non-DP setting
    # Collate backward messages
    mu_back_contact_log = np.zeros((num_time_steps, 4), dtype=np.float32)
    for row in backward_messages:
      if row[1] < 0:
        break
      # assert user == row[1], f"User {user} is not {row[1]}"
      _, timestep, message = int(row[0]), int(row[2]), row[3:]
      mu_back_contact_log[timestep] += np.log(message + 1E-12)
    mu_back_contact_log -= np.max(mu_back_contact_log)
    mu_back_contact = np.exp(mu_back_contact_log)
    mu_back_contact /= np.expand_dims(np.sum(mu_back_contact, axis=1), axis=1)

    # Clip messages in case of quantization
    mu_back_contact = np.minimum(
      0.9999, np.maximum(mu_back_contact, 0.0001)).astype(np.float32)

  mu_f2v_forward = -1 * np.ones((num_time_steps, 4), dtype=np.float32)
  mu_f2v_backward = -1 * np.ones((num_time_steps, 4), dtype=np.float32)

  # Forward messages can be interpreted as modifying the dynamics matrix.
  # Therefore, we precompute these matrices using the incoming forward messages
  A_user = adjust_matrices_map(
    A_matrix, p1, forward_messages, num_time_steps, clip_lower, clip_upper,
    epsilon_dp=epsilon_dp, a_rdp=a_rdp)

  betas = np.zeros((num_time_steps, 4))
  # Move all messages forward
  mu_f2v_forward[0] = np.array([1.-p0, p0, 1E-9, 1E-9], dtype=np.float32)
  for t_now in range(1, num_time_steps):
    mu_f2v_forward[t_now] = A_user[t_now-1].T.dot(
      mu_f2v_forward[t_now-1] * obs_messages[t_now-1]
      * mu_back_contact[t_now-1]) + np.float32(1E-12)

  # Move all messages backward
  mu_f2v_backward[num_time_steps-1] = np.ones((4), dtype=np.float32)
  for t_now in range(num_time_steps-2, -1, -1):
    mu_f2v_backward[t_now] = A_user[t_now].dot(
      mu_f2v_backward[t_now+1] * obs_messages[t_now+1]
      * mu_back_contact[t_now+1]) + np.float32(1E-12)

  # Collect marginal beliefs
  betas = mu_f2v_forward * mu_f2v_backward * obs_messages * mu_back_contact
  betas += np.float32(1E-12)
  betas /= np.expand_dims(np.sum(betas, axis=1), axis=1)

  # Calculate messages backward
  max_num_messages = constants.CTC
  messages_send_back = -1 * np.ones((max_num_messages, 7), dtype=np.float32)
  # TODO: unfreeze backward messages
  if epsilon_dp < 0:   # Only calculate bwd messages when non-DP
    for num_row in numba.prange(max_num_messages):  # pylint: disable=not-an-iterable
      user_backward = int(forward_messages[num_row][0])
      if user_backward < 0:
        continue

      timestep_back = int(forward_messages[num_row][2])
      p_message = float(forward_messages[num_row][3])
      A_back = A_user[timestep_back]

      # This is the term that needs cancelling due to the forward message
      p_transition = A_back[0][0] / (p_message * (1-p1) + (1-p_message) * 1)

      # Cancel the terms in the two dynamics matrices
      A_back_0 = np.copy(A_back)
      A_back_1 = np.copy(A_back)
      A_back_0[0][0] = p_transition  # S --> S
      A_back_0[0][1] = 1. - p_transition  # S --> E
      A_back_1[0][0] = p_transition * (1-p1)  # S --> S
      A_back_1[0][1] = 1. - p_transition * (1-p1)  # S --> E

      # Calculate the SER terms and calculate the I term
      mess_SER = np.sum(
        A_back_0.dot(mu_f2v_backward[timestep_back+1]
                     * obs_messages[timestep_back+1])
        * mu_f2v_forward[timestep_back] * obs_messages[timestep_back])
      mess_I = np.sum(
        A_back_1.dot(mu_f2v_backward[timestep_back+1]
                     * obs_messages[timestep_back+1])
        * mu_f2v_forward[timestep_back] * obs_messages[timestep_back])
      message_back = np.array([mess_SER, mess_SER, mess_I, mess_SER]) + 1E-12
      message_back /= np.sum(message_back)

      # Collect backward message
      array_back = np.array(
        [user, user_backward, timestep_back,
         message_back[0], message_back[1], message_back[2], message_back[3]],
        dtype=np.float32)
      messages_send_back[num_row] = array_back
    messages_send_back = messages_send_back.astype(np.float32)
  else:
    # On positive a_rdp, we send back uniform messages, because noised up
    #  forward messages may generate NaN. TODO: fix this
    messages_send_back[:, 0] = user * np.ones(
      (max_num_messages), dtype=np.float32)  # user sending
    messages_send_back[:, 1] = forward_messages[:, 0]  # user receiving
    messages_send_back[:, 2] = forward_messages[:, 2]  # timestep
    messages_send_back[:, 3:7] = 0.25  # uniform message

    # Set unused messages to -1
    num_fwd_messages = int(np.sum(forward_messages[:, 0] >= 0))
    messages_send_back[num_fwd_messages:] = -1.
    messages_send_back = messages_send_back.astype(np.float32)

  # Calculate messages forward
  messages_send_forward = -1 * np.ones((max_num_messages, 4), dtype=np.float32)
  if epsilon_dp < 0:  # Only take out bwd messages when non-DP
    # for row in backward_messages:
    for num_row in numba.prange(max_num_messages):  # pylint: disable=not-an-iterable
      user_forward = int(backward_messages[num_row][0])
      if user_forward < 0:
        continue

      timestep = int(backward_messages[num_row][2])
      msg_back = backward_messages[num_row][3:7]

      # Calculate forward message
      message_backslash = util.normalize(msg_back + 1E-12)
      # TODO: do in logspace
      message = betas[timestep] / message_backslash
      message /= np.sum(message)

      array_fwd = np.array(
        [user, user_forward, timestep, message[2]], dtype=np.float32)
      messages_send_forward[num_row] = array_fwd
  else:
    messages_send_forward[:, 0] = user * np.ones(
      (max_num_messages), dtype=np.float32)  # user sending
    messages_send_forward[:, 1] = backward_messages[:, 0]  # user receiving
    messages_send_forward[:, 2] = backward_messages[:, 2]  # timestep

    timestep_array = backward_messages[:, 2].astype(np.int32)
    messages_send_forward[:, 3] = np.take(
      betas[:, 2], timestep_array)

    # Set unused messages to -1
    num_bwd_messages = int(np.sum(backward_messages[:, 0] >= 0))
    messages_send_forward[num_bwd_messages:] = -1.
    messages_send_forward = messages_send_forward.astype(np.float32)

  return betas, messages_send_back, messages_send_forward


def init_message_maps(
    contacts_all: np.ndarray,
    user_interval: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray]:
  """Initialises the message maps."""
  # Put backward messages in hashmap, such that they can be overwritten when
  #   doing multiple iterations in loopy belief propagation
  num_users_interval = user_interval[1] - user_interval[0]
  max_num_contacts = constants.CTC
  map_backward_message = -1 * np.ones((num_users_interval, max_num_contacts, 7))
  map_forward_message = -1 * np.ones((num_users_interval, max_num_contacts, 4))

  num_bw_message = np.zeros((num_users_interval), dtype=np.int32)
  num_fw_message = np.zeros((num_users_interval), dtype=np.int32)

  for contact in contacts_all:
    user_u = int(contact[0])
    user_v = int(contact[1])

    # Backward message:
    if user_interval[0] <= user_u < user_interval[1]:
      # Messages go by convention of
      # [user_send, user_receive, timestep, *msg]
      msg_bw = np.array(
        [user_v, user_u, contact[2], .25, .25, .25, .25], dtype=np.float32)
      user_rel = user_u - user_interval[0]
      map_backward_message[user_rel][num_bw_message[user_rel]] = msg_bw
      num_bw_message[user_rel] += 1

    # Forward message:
    if user_interval[0] <= user_v < user_interval[1]:
      # Messages go by convention of
      # [user_send, user_receive, timestep, *msg]
      msg_fw = np.array([user_u, user_v, contact[2], 0.], dtype=np.float32)
      user_rel = user_v - user_interval[0]
      map_forward_message[user_rel][num_fw_message[user_rel]] = msg_fw
      num_fw_message[user_rel] += 1
  return (
    map_forward_message.astype(np.single),
    map_backward_message.astype(np.single))


@numba.njit(parallel=True)
def do_backward_forward_subset(
    user_interval: Tuple[int, int],
    A_matrix: np.ndarray,
    p0: float,
    p1: float,
    num_time_steps: int,
    obs_messages: np.ndarray,
    map_backward_message: np.ndarray,
    map_forward_message: np.ndarray,
    clip_lower: float = -1.,
    clip_upper: float = 10000.,
    epsilon_dp: float = -1.,
    a_rdp: float = -1.,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
  """Does forward backward on a subset of users in sequence.

  Note, the messages are appended, and thus not updated between users!
  """
  num_users_interval = user_interval[1] - user_interval[0]
  bp_beliefs_subset = -1 * np.ones(
    (num_users_interval, num_time_steps, 4), dtype=np.float32)

  # Init ndarrays for all messages being sent by users in this subset
  messages_backward_subset = -1 * np.ones(
    (num_users_interval, constants.CTC, 7), dtype=np.float32)
  messages_forward_subset = -1 * np.ones(
    (num_users_interval, constants.CTC, 4), dtype=np.float32)

  for user_id in numba.prange(num_users_interval):  # pylint: disable=not-an-iterable
    (
      bp_beliefs_subset[user_id],
      messages_backward_subset[user_id],
      messages_forward_subset[user_id]) = (
        forward_backward_user(
          A_matrix, p0, p1,
          user_id+user_interval[0],
          map_backward_message[user_id],
          map_forward_message[user_id],
          num_time_steps,
          obs_messages[user_id],
          clip_lower,
          clip_upper,
          epsilon_dp=epsilon_dp,
          a_rdp=a_rdp))

  return bp_beliefs_subset, messages_forward_subset, messages_backward_subset


@numba.njit
def do_backward_forward_and_message(
    A_matrix: np.ndarray,
    p0: float,
    p1: float,
    num_time_steps: int,
    obs_messages: np.ndarray,
    num_users: int,
    map_backward_message: np.ndarray,
    map_forward_message: np.ndarray,
    user_interval: Tuple[int, int],
    clip_lower: float = -1.,
    clip_upper: float = 10000.,
    epsilon_dp: float = -1.,
    a_rdp: float = -1.,
    quantization: Optional[int] = -1,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Tuple[float, float, float]]:
  """Runs forward and backward messages for one user and collates messages."""
  with numba.objmode(t0='f8'):
    t0 = time.time()

  bp_beliefs, msg_list_fwd, msg_list_bwd = do_backward_forward_subset(
    user_interval=user_interval,
    A_matrix=A_matrix,
    p0=p0,
    p1=p1,
    num_time_steps=num_time_steps,
    obs_messages=obs_messages,
    map_backward_message=map_backward_message,
    map_forward_message=map_forward_message,
    clip_lower=clip_lower,
    clip_upper=clip_upper,
    epsilon_dp=epsilon_dp,
    a_rdp=a_rdp)

  with numba.objmode(t1='f8'):
    t1 = time.time()

  # Sort by receiving user
  # Array in [num_users, max_num_messages, num_elements]
  map_backward_message = util_bp.flip_message_send(
    msg_list_bwd, num_users, do_bwd=True)
  # Array in [num_users, max_num_messages, num_elements]
  map_forward_message = util_bp.flip_message_send(
    msg_list_fwd, num_users, do_bwd=False)

  # Do quantization
  map_backward_message[:, :, 3:] = util.quantize_floor(
    map_backward_message[:, :, 3:], quantization).astype(np.single)
  map_forward_message[:, :, 3] = util.quantize_floor(
    map_forward_message[:, :, 3], quantization).astype(np.single)

  # Assert that beliefs sum to 1
  # np.testing.assert_array_almost_equal(
  #   np.sum(bp_beliefs, axis=-1), 1., decimal=3)

  with numba.objmode(t2='f8'):
    t2 = time.time()

  return bp_beliefs, map_backward_message, map_forward_message, (t0, t1, t2)
