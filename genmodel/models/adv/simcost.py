import contextlib
import sys

import torch
from torch import autograd
from torch.nn import functional as F

from .arch import Discriminator, Generator
from ..baseadv import BaseAdversarial

sys.path.append('..')
import renyi


def cosine_kernel(min_cosine=1e-6):
    return lambda x, y: renyi.generic_kernel(x, y, lambda u, v: (
        renyi.cosine_similarity(u, v) + 1 + min_cosine) / (2 + min_cosine))


class SimilarityCostModel(BaseAdversarial):

    def __init__(self, flags, *args, **kwargs):
        generator = Generator(flags.z_size, flags.h_size)
        discriminator = Discriminator(flags.h_size // 2, out_size=flags.v_size)
        super().__init__(flags, generator, discriminator, *args, **kwargs)
        if flags.unbiased > 0:
            self.batch_size = flags.batch_size // 2
        else:
            self.batch_size = flags.batch_size
        uniform = torch.ones(1, self.batch_size, device=self.device)
        self.uniform = uniform / uniform.sum()
        self.kernel = cosine_kernel()

    def loss_function(self, forward_ret, labels=None):
        x_gen, x_real = forward_ret
        if self.debug:
            debug_context = autograd.detect_anomaly()
        else:
            debug_context = contextlib.nullcontext()
        with debug_context:
            v_real = self.disc(x_real)
            v_gen = self.disc(x_gen)

        D = lambda x, y: renyi.renyi_mixture_divergence(self.uniform, x, self.uniform, y, self.kernel, self.flags.alpha,
                                                        use_full=self.flags.use_full, use_avg=self.flags.use_avg,
                                                        symmetric=self.flags.symmetric)
        if self.flags.unbiased == 0:
            div = D(v_real, v_gen)
        else:
            x_prime = v_real[:self.batch_size]
            x = v_real[self.batch_size:]
            y_prime = v_gen[:self.batch_size]
            y = v_gen[self.batch_size:]
            div = D(x, y) + D(x, y_prime) + D(x_prime, y) + \
                D(x_prime, y_prime) - 2 * D(y, y_prime) - 2 * D(x, x_prime)

        if self.train_disc():
            loss = -div
            self.d_loss = loss.item()
        else:
            loss = div
            self.g_loss = loss.item()

        return loss, self.g_loss, self.d_loss
