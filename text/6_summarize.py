from collections import deque
import glob
import pickle
import sys
sys.path.append('..')

from matplotlib import pyplot as plt
import numpy as np
import torch
from torch import nn, optim
from torch.nn import functional as F
from wordcloud import WordCloud
import matplotlib.pyplot as plt

from renyi import mink_sim_divergence, renyi_sim_entropy, renyi_sim_divergence, rbf_kernel, \
    renyi_mixture_divergence_stable
import utils
if torch.cuda.is_available():
    device = "cuda"
else:
    device = "cpu"


def get_summary_movable_locs(words, probs, embs, all_embs, all_words, k=25, scale=.1, autoscale=True):
    p = torch.tensor(np.array(probs, dtype=np.float32)[None, ...])
    if autoscale:
        dist = utils.batch_pdist(embs, embs, p=2)
        scale = scale*(torch.var(dist))
    kernel = lambda x, y: rbf_kernel(x, y, sigmas=[scale], log=True)
    locs = ((torch.randn_like(embs))[:k] - torch.mean(embs))*torch.std(embs)
    q_logits = torch.zeros(1, k)

    p = p.to(device)
    q_logits = q_logits.to(device).detach().requires_grad_()
    locs = locs.to(device).detach().requires_grad_()
    embs = embs.to(device).detach()
    q_optimizer = optim.Adam([q_logits, locs], lr=1e-4, betas=(0.5, 0.999), amsgrad=False)
    converged = False
    recent = deque(maxlen=1000)
    step = 0
    while step < 100000 and not converged:
        q = F.softmax(q_logits, dim=1)
        div = renyi_mixture_divergence_stable(p, embs, q, locs, log_kernel=kernel, alpha=1)

        div_item = div.item()
        loss = div

        # if (step - 1) % 100 == 0:
        #     print(locs[0])

        if step % 100 == 0:
            big_locs = torch.cat([embs, locs], 0)
            big_q = torch.cat([torch.zeros_like(p), q], 1)
            K = rbf_kernel(big_locs, big_locs, sigmas=[scale])
            print(torch.mean(K).item())
            print('Step %d: %0.4f' % (step, div_item))
            # inp = sorted(list(zip(q[0], words)), reverse=True)
            # print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
            inp = sorted(list(zip((K@big_q[0]), words)), reverse=True)
            print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
            # inp = sorted([i.item() for i in q_logits[0]], reverse=True)
            # print(inp)
        recent.append(loss.item())
        loss.backward()
        q_optimizer.step()

        if -np.min(recent) + np.mean(recent) < 1e-4 and step > 1000:
            converged = True
        step += 1

    big_locs = torch.cat([embs, locs], 0)
    big_q = torch.cat([torch.zeros_like(p), q], 1)
    K = rbf_kernel(big_locs, big_locs, sigmas=[scale])
    q = q[0]
    Kq = K @ big_q[0]
    loc_dists = utils.batch_pdist(embs, locs, p=2)
    # import ipdb; ipdb.set_trace()
    closest_words = torch.min(loc_dists, 0)[1]
    closest_words = [words[idx] for idx in closest_words]
    dist = utils.batch_pdist(embs, embs, p=2)
    K = torch.exp(-scale*dist**2)
    Kp = K @ p[0]
    inp = sorted(list(zip(probs, words)), reverse=True)
    print('\nInput:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
    inp = sorted(list(zip(Kq, words)), reverse=True)
    print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
    # inp = sorted(list(zip(q, words)), reverse=True)
    # print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])

    inp = sorted(list(zip(q, closest_words)), reverse=True)
    print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])

    return Kq, Kp, q, locs, closest_words

def print_summary(words, probs, embs, scale=200):
    p = torch.tensor(np.array(probs, dtype=np.float32)[None, ...])
    # norm = torch.norm(embs, p=2, dim=1, keepdim=True)
    # cosine = (embs @ embs.t()) / norm / norm.t()
    # K = torch.exp(1.0 * (cosine - 1))  # 2 to increase scale

    dist = utils.batch_pdist(embs, embs, p=2)
    K = torch.exp(-scale*dist**2)
    plt.imshow(K)
    plt.colorbar()
    plt.show()

    Kp = K @ p[0]

    q_logits = (0*torch.randn_like(p))
    # q_logits = (10 + torch.log(p)).requires_grad_()

    p = p.to(device)
    q_logits = q_logits.to(device).detach().requires_grad_()
    K = K.to(device)
    q_optimizer = optim.Adam([q_logits], lr=1e-3, betas=(0.9, 0.999), amsgrad=False)
    converged = False
    recent = deque(maxlen=1000)
    step = 0
    while step < 100000 and not converged:
        lda = 0.1*max(min(step/10000., 1.), 0)
        q = F.softmax(q_logits, dim=1)
        # div = mink_sim_divergence(K, p, q, use_inv=False) + 0.005*renyi_sim_entropy(K, q) #0.005*torch.sum(q * utils.clamp_log(q))
        div = renyi_sim_divergence(K, p, q)
        reg = lda * torch.norm(q, p=0.5)#  # +\
              #torch.sum(torch.sigmoid((q_logits - torch.mean(q_logits))))
        # div = renyi_sim_divergence(K, p, q) - min(step / 5000., 3.) * torch.clamp(torch.sum(q*utils.clamp_log(q)), 3)
        div_item = div.item()
        loss = div+reg
        loss_item = loss.item()

        if (step - 1) % 100 == 0:
            print(-np.min(recent) + np.mean(recent), np.min(recent), np.mean(recent))

        if step % 1000 == 0:
            print('Step %d: %0.4f; %0.4f' % (step, div_item, loss_item))
            inp = sorted(list(zip(q[0], words)), reverse=True)
            print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
            inp = sorted(list(zip((K@q[0]), words)), reverse=True)
            print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
            # inp = sorted([i.item() for i in q_logits[0]], reverse=True)
            # print(inp)
        recent.append(loss.item())
        loss.backward()
        q_optimizer.step()

        if -np.min(recent) + np.mean(recent) < 1e-4 and step > 15000:
            converged = True
        step += 1
    q = q[0]
    Kq = K @ q
    inp = sorted(list(zip(probs, words)), reverse=True)
    print('\nInput:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
    inp = sorted(list(zip(Kq, words)), reverse=True)
    print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
    inp = sorted(list(zip(q, words)), reverse=True)
    print('\nSummary:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])

    return Kq, Kp, q


def wordcloud(probs, words, name, thresh=0.01, vsize=5):
    frequencies = {w.upper():p.item() for w, p in zip(words, probs) if p>thresh}
    cloud = WordCloud(width=500, height=int(vsize*100), background_color="white", colormap="tab20").generate_from_frequencies(frequencies)

    plt.clf()
    plt.figure(figsize=(5, vsize))
    plt.imshow(cloud, interpolation='bilinear', aspect="equal")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(name+".pdf", figsize=(5, vsize), dpi=100)
    #
    # # lower max_font_size
    # plt.clf()
    # cloud = WordCloud(background_color="white", max_font_size=40, colormap="tab20").generate_from_frequencies(frequencies)
    # plt.figure()
    # plt.imshow(cloud, interpolation="bilinear")
    # plt.axis("off")
    # plt.show()
    # plt.savefig(name+"_small.pdf")


if __name__ == '__main__':
    with open('data/news_words', 'rb') as f:
        all_words = pickle.load(f)
    with open('data/tfidf', 'rb') as f:
        tdidf_articles = pickle.load(f)
    with open('data/transformed_vectors', 'rb') as f:
        word_embs = pickle.load(f)
    word_embs = torch.tensor(word_embs, dtype=torch.float32)

    fnames = sorted(glob.glob('data/EnglishProcessed/*.txt'))
    for i, article_fname in enumerate(fnames[5:10], 5):
        feature_index = tdidf_articles[i, :].nonzero()[1]
        if not feature_index.size:
            continue

        with open(article_fname, 'r') as f:
            article = f.read().strip()
        print(article)
        print('-----')
        words = [all_words[x] for x in feature_index]
        probs = [tdidf_articles[i, x] for x in feature_index]
        wordcloud(probs, words, "figs/cloud_{}_tfidf".format(i-5), 0)
        assert np.isclose(np.sum(probs), 1.0)
        embs = torch.stack([word_embs[x] for x in feature_index])
        inp = sorted(list(zip(probs, words)), reverse=True)
        print('\nInput:', ['%s %0.2f' % (w, p * 100) for (p, w) in inp])
        print()

        Kq, Kp, q = get_summary_movable_locs(words, probs, embs, k=10, all_embs=word_embs, all_words=all_words)

        Kq, Kp, q = print_summary(words, probs, embs)

        count = torch.sum(q > 0.01).int().item()
        reduced = inp[:count]
        wordcloud([p for p, w in reduced], [w for p, w in reduced], "figs/cloud_{}_tfidf_top_{}".format(i-5, count), 0)

        wordcloud(q, words, "figs/cloud_{}".format(i-5), thresh=0.01, vsize=2.5)

        bar_indices = sorted(list(zip(Kp, range(len(feature_index)))), reverse=True)
        bar_data = list(zip(*[(i, p.item() * 100) for (i, (p, w)) in enumerate(bar_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)

        plot_indices = [q[w] for (_, w) in bar_indices]
        bar_data = list(zip(*[(i, p.item() * 100) for (i, p) in enumerate(plot_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)
        plt.savefig("KP_plot.pdf")
        plt.show()

        bar_indices = sorted(list(zip(probs, range(len(feature_index)))), reverse=True)
        bar_data = list(zip(*[(i, p * 100) for (i, (p, w)) in enumerate(bar_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)

        plot_indices = [q[w] for (_, w) in bar_indices]
        bar_data = list(zip(*[(i, p.item() * 100) for (i, p) in enumerate(plot_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)
        plt.show()
        plt.savefig("Probs_plot.pdf")

        bar_indices = sorted(list(zip(Kp, range(len(feature_index)))), reverse=True)
        plot_indices = [probs[w] for (_, w) in bar_indices]
        bar_data = list(zip(*[(i, p * 100) for (i, p) in enumerate(plot_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)

        plot_indices = [q[w] for (_, w) in bar_indices]
        bar_data = list(zip(*[(i, p.item() * 100) for (i, p) in enumerate(plot_indices)]))
        plt.bar(*bar_data, width=1.0, alpha=0.5)
        plt.show()
        plt.savefig("KP_plot_2.pdf")

        print("===============")
        print('\n')
