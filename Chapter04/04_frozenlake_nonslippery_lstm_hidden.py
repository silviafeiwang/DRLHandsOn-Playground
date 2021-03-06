#!/usr/bin/env python3
import random
import gym
import gym.spaces
import gym.wrappers
import gym.envs.toy_text.frozen_lake
from collections import namedtuple
import numpy as np
from tensorboardX import SummaryWriter
from os import path
import project_root
from datetime import datetime

import torch
import torch.nn as nn
import torch.optim as optim

NUM_LAYERS = 1
HIDDEN_SIZE = 128
BATCH_SIZE = 100
PERCENTILE = 30
GAMMA = 0.9

log_file = path.join(project_root.DIR, 'Chapter04', 'experiment_data', '04_frozenlake_nonslippery_lstm_hidden.txt')
with open(log_file, 'a') as f:
    f.write(datetime.now().strftime("\n\n\n\n%Y-%m-%d %H-%M-%S\n\n"))


class DiscreteOneHotWrapper(gym.ObservationWrapper):
    def __init__(self, env):
        super(DiscreteOneHotWrapper, self).__init__(env)
        assert isinstance(env.observation_space, gym.spaces.Discrete)
        self.observation_space = gym.spaces.Box(0.0, 1.0, (env.observation_space.n, ), dtype=np.float32)

    def observation(self, observation):
        res = np.copy(self.observation_space.low)
        res[observation] = 1.0
        return res


class Net(nn.Module):
    def __init__(self, obs_size, hidden_size, n_actions, num_layers):
        super(Net, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(obs_size, hidden_size, num_layers, batch_first=True)

        self.policy = nn.Linear(hidden_size, n_actions)

    def init_hidden(self, batch_size=1):
        return (torch.zeros(self.num_layers, batch_size, self.hidden_size),
                torch.zeros(self.num_layers, batch_size, self.hidden_size))

    def forward(self, x, hidden=None):
        if hidden is None:
            lstm_out, hidden = self.lstm(x)
        else:
            hn, cn = hidden[0], hidden[1]
            lstm_out, hidden = self.lstm(x, (hn, cn))
        probs = self.policy(lstm_out[:, -1, :])
        return probs, hidden


Episode = namedtuple('Episode', field_names=['reward', 'steps'])
EpisodeStep = namedtuple('EpisodeStep', field_names=['observation', 'action', 'hn', 'cn'])


def iterate_batches(env, net, batch_size):
    batch = []
    episode_reward = 0.0
    episode_steps = []
    obs = [env.reset()]
    sm = nn.Softmax(dim=1)
    prev_hidden = net.init_hidden()
    while True:
        obs_v = torch.FloatTensor([obs])
        act_probs, hidden = net(obs_v, prev_hidden)
        act_probs_v = sm(act_probs)
        act_probs = act_probs_v.data.numpy()[0]
        action = np.random.choice(len(act_probs), p=act_probs)
        next_obs, reward, is_done, _ = env.step(action)
        episode_reward += reward
        # print(hidden[0])
        hn = prev_hidden[0].view(1, NUM_LAYERS, HIDDEN_SIZE).data.numpy()[0]
        cn = prev_hidden[1].view(-1, NUM_LAYERS, HIDDEN_SIZE).data.numpy()[0]
        # print(hn)
        episode_steps.append(EpisodeStep(observation=obs, action=action, hn=hn, cn=cn))
        if is_done:
            batch.append(Episode(reward=episode_reward, steps=episode_steps))
            episode_reward = 0.0
            episode_steps = []
            next_obs = env.reset()
            hidden = net.init_hidden()
            if len(batch) == batch_size:
                yield batch
                batch = []
        obs = [next_obs]
        prev_hidden = hidden


def filter_batch(batch, percentile):
    disc_rewards = list(map(lambda s: s.reward * (GAMMA ** len(s.steps)), batch))
    reward_bound = np.percentile(disc_rewards, percentile)

    train_obs = []
    train_act = []
    train_hn = []
    train_cn = []
    elite_batch = []
    for example, discounted_reward in zip(batch, disc_rewards):
        if discounted_reward > reward_bound:
            train_obs.extend(map(lambda step: step.observation, example.steps))
            train_act.extend(map(lambda step: step.action, example.steps))
            train_hn.extend(map(lambda step: step.hn, example.steps))
            train_cn.extend(map(lambda step: step.cn, example.steps))
            elite_batch.append(example)

    return elite_batch, train_obs, train_act, train_hn, train_cn, reward_bound


if __name__ == "__main__":
    random.seed(12345)
    env = gym.envs.toy_text.frozen_lake.FrozenLakeEnv(is_slippery=False)
    env = gym.wrappers.TimeLimit(env, max_episode_steps=100)
    env = DiscreteOneHotWrapper(env)
    # env = gym.wrappers.Monitor(env, directory="mon", force=True)
    obs_size = env.observation_space.shape[0]
    n_actions = env.action_space.n

    net = Net(obs_size, HIDDEN_SIZE, n_actions, NUM_LAYERS)
    objective = nn.CrossEntropyLoss()
    optimizer = optim.Adam(params=net.parameters(), lr=0.001)
    writer = SummaryWriter(comment="-frozenlake-nonslippery-lstm-hidden")

    full_batch = []
    for iter_no, batch in enumerate(iterate_batches(env, net, BATCH_SIZE)):
        reward_mean = float(np.mean(list(map(lambda s: s.reward, batch))))
        full_batch, obs, acts, hn, cn, reward_bound = filter_batch(full_batch + batch, PERCENTILE)
        if not full_batch:
            continue
        obs_v = torch.FloatTensor(obs)
        acts_v = torch.LongTensor(acts)
        full_batch = full_batch[-500:]

        optimizer.zero_grad()
        hn = torch.FloatTensor(hn).view(NUM_LAYERS, -1, HIDDEN_SIZE)
        cn = torch.FloatTensor(cn).view(NUM_LAYERS, -1, HIDDEN_SIZE)
        action_scores_v, _ = net(obs_v, (hn, cn))
        loss_v = objective(action_scores_v, acts_v)
        loss_v.backward()
        optimizer.step()
        print("%d: loss=%.3f, reward_mean=%.3f, reward_bound=%.3f, batch=%d" % (
            iter_no, loss_v.item(), reward_mean, reward_bound, len(full_batch)))
        with open(log_file, 'a') as f:
            f.write("%d: loss=%.3f, reward_mean=%.3f, reward_bound=%.3f, batch=%d \n" % (
            iter_no, loss_v.item(), reward_mean, reward_bound, len(full_batch)))
        writer.add_scalar("loss", loss_v.item(), iter_no)
        writer.add_scalar("reward_mean", reward_mean, iter_no)
        writer.add_scalar("reward_bound", reward_bound, iter_no)
        if reward_mean > 0.8:
            print("Solved!")
            break
    writer.close()
