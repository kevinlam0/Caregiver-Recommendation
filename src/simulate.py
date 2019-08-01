import os
import argparse
from collections import deque, defaultdict
import json
import numpy as np
import matplotlib.pyplot as plt
from alg import LinUCB, MultiLinUCB, UniformRandom

class Stats:
  '''
  Simulate time statistics
  '''

  def __init__(self, n_choices):
    self.time_gap = lambda: np.random.poisson(10)
    self.expire_after = 180
    self.history = deque()

    self.vct = np.array([0] * n_choices)
    self.time = 0

    # init time for the first event
    self.time_pass()

  def time_pass(self):
    # random time pass
    self.time += self.time_gap()
    while self.history \
      and self.history[0]['time'] + self.expire_after <= self.time:
      recomm = self.history.popleft()
      self.vct[recomm['action']] -= 1

  def update(self, action):
    if action is None:
      return

    self.history.append({
      'action': action,
      'time': self.time
    })
    self.vct[action] += 1

  def reset(self):
    self.time = 0
    self.vct.fill(0)
    self.history = deque()


class UserProfile:
  def __init__(self, ctx_size, n_choices):
    self.weight = np.concatenate([
      np.random.randn(n_choices, ctx_size),
      np.random.normal(-4, .5, (n_choices, n_choices))
    ], axis=1)

    self.stats = Stats(n_choices)

    self.choice_history = []

class Scenario:
  '''
  Linear scenario
  '''

  def __init__(self, ctx_size, n_choices, n_users, noise_scale=0.1):
    self.ctx_size = ctx_size
    self.n_choices = n_choices
    self.n_users = n_users

    self.global_weight = np.concatenate([
      np.random.randn(n_choices, ctx_size),
      np.random.normal(-5, 1, (n_choices, n_choices))
    ], axis=1)

    self.user_profiles = [UserProfile(ctx_size, n_choices) for i in range(n_users)]

    self.ctx = None

    self.noise_scale = noise_scale
    self.noise = lambda: np.random.normal(scale=noise_scale)


  @property
  def payload(self):
    return {
      'ctx_size': self.ctx_size,
      'n_choices': self.n_choices,
      'noise_scale': self.noise_scale,
      'global_weight': self.global_weight.tolist(),
      'user_profiles': [u.weight for u in self.user_profiles]
    }

  @classmethod
  def load(cls, payload):
    scenario = cls(payload['ctx_size'], payload['n_choices'], payload['noise_scale'])
    scenario.global_weight = np.array(payload['global_weight'])
    scenario.user_profiles = [UserProfile(payload['ctx_size'], payload['n_choices']) for _ in payload['user_profiles']]
    for u, weight in zip(scenario.user_profiles, payload['user_profiles']):
      u.weight = weight

    return scenario

  def next_event(self):
    ''' Sample the next event and return it '''

    # choose the user with the earliest timestamp
    user_idx = np.argmin([u.stats.time for u in self.user_profiles])
    user = self.user_profiles[user_idx]
    time = user.stats.time

    ctx = np.concatenate([
      np.random.normal(0, 1, self.ctx_size),
      user.stats.vct
    ])

    session = ScenarioSession(self, user_idx, ctx, time)

    return session

  def reward(self, choice):
    # return self.weight[choice] @ self.ctx + self.noise()
    pass

  def insight(self, user_idx, ctx, choice):
    ''' Return both the reward & regret '''

    user = self.user_profiles[user_idx]

    truth = [v + self.noise() for v in (user.weight + self.global_weight) @ ctx]

    # append zero for no feedback
    truth.append(0)
    opt_reward = max(truth)
    if choice is not None:
      reward = truth[choice]
    else:
      reward = 0

    return reward, opt_reward - reward

  def reset_stats(self):
    for u in user_profiles:
      self.u.stats.reset()


class ScenarioSession:
  '''
  Session of each scenario ctx event to be used in simulator
  '''
  def __init__(self, scenario, user_idx, ctx, time):
    self.scenario = scenario
    self.user_idx = user_idx
    self.ctx = ctx
    self.time = time

    self.choice, self.reward, self.regret = None, None, None

  @property
  def user(self):
    return self.scenario.user_profiles[self.user_idx]

  def recomm(self, choice):
    ''' Recommend the choice & Return both the reward & regret '''

    assert self.choice is None

    self.choice = choice

    self.reward, self.regret = self.scenario.insight(self.user_idx, self.ctx, choice)

    self.user.stats.update(choice)
    self.user.stats.time_pass()

    return self.reward, self.regret



class IgnoreTaskAlgAdapter:
  def __init__(self, alg_cls, alg_args=(), alg_kargs={}):
    self.alg = alg_cls(*alg_args, **alg_kargs)

  def act(self, session):
    return self.alg.act(session.ctx)

  def update(self, session):
    return self.alg.update(session.ctx, session.choice, session.reward)

class SingleTaskAlgAdapter:
  ''' Adaptor for SingleTask Algorithm '''

  def __init__(self, alg_cls, alg_args=(), alg_kargs={}, n_tasks=1):
    self.algs = [alg_cls(*alg_args, **alg_kargs) for _ in range(n_tasks)]

  def act(self, session):
    return self.algs[session.user_idx].act(session.ctx)

  def update(self, session):
    return self.algs[session.user_idx].update(session.ctx, session.choice, session.reward)

class MultiTaskAlgAdapter:
  ''' Adaptor for MultiTask Algorithm '''

  def __init__(self, alg_cls, alg_args=(), alg_kargs={}):
    self.alg = alg_cls(*alg_args, **alg_kargs)

  def act(self, session):
    return self.alg.act(session.user_idx, session.ctx)

  def update(self, session):
    return self.alg.update(session.user_idx, session.ctx, session.choice, session.reward)

class Simulator:
  def __init__(self, scenario):
    self.scenario = scenario
    self.regrets = [0]
    self.choice_history = []

    self.training_data = None

  def init(self, alg_cls, alg_type='single', alg_args=()):
    if alg_type == 'multi':
      self.alg = MultiTaskAlgAdapter(alg_cls, alg_args)
    else:
      self.alg = SingleTaskAlgAdapter(alg_cls, alg_args, n_tasks=self.scenario.n_users)

  def train(self, iters):
    assert iters <= len(self.training_data)

    data = self.training_data[:iters]
    for ctx, choice, reward in data:
      # TODO fix here
      self.alg.update(np.array(ctx), choice, reward)

  def test(self, iters):
    accum_regret = 0

    for i in range(iters):
      session = self.scenario.next_event()
      choice = self.alg.act(session)
      reward, regret = session.recomm(choice)
      self.alg.update(session)

      self.choice_history.append(session)

  def run(self, test_iters, train_iters=0):
    if train_iters:
      self.train(train_iters)

    regrets = self.test(test_iters)
    return regrets

  def save(self, path):
    history = [
      [s.user_idx, s.ctx, s.choice, s.reward, s.time]
      for s in self.choice_history if s.choice is not None
    ]
    payload = {
      'scenario': self.scenario.payload,
      'history': history
    }

    def serialize(o):
      if isinstance(o, np.ndarray):
        return o.tolist()
      if isinstance(o, np.int64):
        return int(o)
      return o

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
      json.dump(payload, f, default=serialize)

  @classmethod
  def load(cls, path):
    with open(path, encoding='utf-8') as f:
      payload = json.load(f)

    scenario = Scenario.load(payload['scenario'])
    instance = cls(scenario)
    instance.training_data = payload['history']

    return instance

  def regret_plot(self, subplot):
    subplot.set_xlabel("Iteration")
    subplot.set_ylabel("Regret")
    subplot.set_title("Accumulated Regret")
    subplot.grid()

    accum_every = 50
    accum_regrets = [0]
    regret_sum = 0
    for i, session in enumerate(self.choice_history):
      regret_sum += session.regret
      if (i + 1) % accum_every == 0:
        accum_regrets.append(regret_sum)

    subplot.plot(list(range(0, len(self.choice_history) + 1, accum_every)), accum_regrets, label='LinUCB')
    subplot.legend(loc='lower right', prop={'size':9})

  def regret_per_user_plot(self, subplot):
    subplot.set_xlabel("Iteration")
    subplot.set_ylabel("Regret")
    subplot.set_title("Accumulated Regret per Task")
    subplot.grid()

    accum_every = 50

    user_history_grp = defaultdict(list)
    for session in self.choice_history:
      user_history_grp[session.user_idx].append(session)

    # sort to ensure the user indice order
    user_history_grp = sorted(user_history_grp.items(), key=lambda t: t[0])
    for user_idx, user_history in user_history_grp:
      accum_regrets = [0]
      regret_sum = 0
      for i, session in enumerate(user_history):
        regret_sum += session.regret
        if (i + 1) % accum_every == 0:
          accum_regrets.append(regret_sum)

      subplot.plot(list(range(0, len(user_history) + 1, accum_every)), accum_regrets, label=f'Task {user_idx}')
    subplot.legend(loc='lower right', prop={'size':9})

  def action_plot(self, subplot):
    subplot.set_xlabel('Time')
    subplot.set_ylabel('Count')
    subplot.set_title("Actions per Duration")
    subplot.grid()

    counts = [[] for _ in range(self.scenario.n_choices + 1)]
    t, step = 0, 60
    for session in self.choice_history:
      if session.time >= t:
        for c_list in counts:
          c_list.append(0)

        t += step

      choice = -1 if session.choice is None else session.choice

      counts[choice][-1] += 1

    for i, c_list in enumerate(counts):
      label = f'Action {i}' if i != len(counts) - 1 else 'No Action'
      # ax_a.plot(list(range(step, t + 1, step)), c_list, label=label)
      btm = [sum(vals[i + 1:]) for vals in zip(*counts)]
      subplot.bar(list(range(step, t + 1, step)), c_list, bottom=btm, width=30, label=label)

    subplot.legend()

  def plot(self):
    fig, (ax_1, ax_2) = plt.subplots(2, sharex=False)

    self.regret_plot(ax_1)
    self.regret_per_user_plot(ax_2)
    # self.action_plot(ax_2)

    plt.show()



def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-a', '--alg', default='LinUCB', help='algorithm to train')
  parser.add_argument('--actions', type=int, default=4, help='number of actions')
  parser.add_argument('--ctx', type=int, default=10, help='context vector size')
  parser.add_argument('--train', type=int, default=0, help='number of training iterations')
  parser.add_argument('--test', type=int, default=100, help='number of testing iterations')
  parser.add_argument('--users', type=int, default=1, help='number of users')
  parser.add_argument('--save', help='path to save the scenario')
  parser.add_argument('--load', help='path to save the scenario')
  args = parser.parse_args()

  if args.load:
    simulator = Simulator.load(args.load)
  else:
    scenario = Scenario(args.ctx, args.actions, args.users)
    simulator = Simulator(scenario)

  alg_type = 'single'
  if args.alg == 'LinUCB':
    alg_cls = LinUCB
    alg_args = (args.ctx + args.actions, args.actions)
  elif args.alg == 'MultiLinUCB':
    alg_cls = MultiLinUCB
    alg_args = (args.ctx + args.actions, args.actions, args.users)
    alg_type = 'multi'
  elif args.alg == 'UniformRandom':
    alg = UniformRandom
    alg_args = (args.actions)
  else:
    exit()

  simulator.init(alg_cls, alg_type=alg_type, alg_args=alg_args)
  simulator.run(args.test, args.train)

  if args.save:
    simulator.save(args.save)
    print('Save scenario at', args.save)

  simulator.plot()

if __name__ == '__main__':
  main()
