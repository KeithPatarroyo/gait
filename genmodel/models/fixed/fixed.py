import sys

import torch
from torch import nn, optim

from pylego.misc import LinearDecay
from pylego import ops

from ..basefixed import BaseFixed

sys.path.append('..')
import renyi


def gaussian_kernel(sigma):
    return lambda x, y: renyi.generic_kernel(x, y, lambda u, v: renyi.rbf_kernel(u, v, sigmas=[sigma], log=True))


def poly_kernel(degree):
    return lambda x, y: renyi.generic_kernel(x, y, lambda u, v: renyi.poly_kernel(u, v, degree=degree, log=True))


class Decoder(nn.Module):

    def __init__(self, z_size, hidden_size, output_size, resnet=False):
        super().__init__()
        if not resnet:
            self.fc = nn.Sequential(
                nn.Linear(z_size, hidden_size),
                nn.BatchNorm1d(hidden_size),
                nn.LeakyReLU(),
                nn.Linear(hidden_size, hidden_size),
                nn.BatchNorm1d(hidden_size),
                nn.LeakyReLU(),
                nn.Linear(hidden_size, output_size)
            )
        else:
            self.fc = nn.Sequential(
                nn.Linear(z_size, z_size * 7 * 7),
                ops.View(-1, z_size, 7, 7),
                ops.ResNet(z_size, [(2, 128, 1), (2, 64, -2), (2, 32, -2), (1, 1, 1)], norm=nn.BatchNorm2d,
                           skip_last_norm=True),
                ops.View(-1, 28 * 28)
            )

    def forward(self, z):
        return torch.sigmoid(self.fc(z))


class Fixed(nn.Module):

    def __init__(self, x_size, h_size, z_size, resnet=False):
        super().__init__()
        self.z_size = z_size
        self.x_z = Decoder(z_size, h_size, x_size, resnet=resnet)

    def forward(self, z):
        return self.x_z((z * 2.0) - 1.0)


class FixedModel(BaseFixed):

    def __init__(self, flags, *args, **kwargs):
        model = Fixed(28 * 28, flags.h_size, flags.z_size, resnet=flags.resnet)
        optimizer = optim.Adam(model.parameters(), lr=flags.learning_rate, betas=(flags.beta1, flags.beta2))
        super().__init__(model, flags, optimizer=optimizer, *args, **kwargs)
        if flags.unbiased > 0:
            self.batch_size = flags.batch_size // 2
        else:
            self.batch_size = flags.batch_size
        uniform = torch.ones(1, self.batch_size, device=self.device)
        self.uniform = uniform / uniform.sum()
        self.alpha_decay = LinearDecay(flags.alpha_decay_start, flags.alpha_decay_end, flags.alpha_initial, flags.alpha)
        if self.flags.kernel == 'gaussian':
            self.kernel = gaussian_kernel(self.flags.kernel_sigma)
        elif self.flags.kernel == 'poly':
            self.kernel = poly_kernel(self.flags.kernel_degree)

    def loss_function(self, forward_ret, labels=None):
        x_gen = forward_ret
        x = labels.view_as(x_gen)
        alpha = self.alpha_decay.get_y(self.get_train_steps())
        D = lambda x, y: renyi.renyi_mixture_divergence_stable(self.uniform, x, self.uniform, y, self.kernel, alpha,
                                                               use_full=self.flags.use_full, use_avg=self.flags.use_avg,
                                                               symmetric=self.flags.symmetric)
        if self.flags.unbiased == 0:
            return D(x, x_gen)
        else:
            x_prime = x[:self.batch_size]
            x = x[self.batch_size:]
            y_prime = x_gen[:self.batch_size]
            y = x_gen[self.batch_size:]
            if self.flags.unbiased == 1:
                return 2 * D(x, y) - D(y, y_prime)
            else:
                return D(x, y) + D(x, y_prime) + D(x_prime, y) + D(x_prime, y_prime) - 2 * D(y, y_prime)
