#!/home/ben/anaconda3/bin/python
from bfore import MapLike, SkyModel
from bfore.components import syncpl, dustmbb, cmb
import numpy as np
import healpy as hp
import matplotlib.pyplot as plt
from os.path import join, abspath

def pixel_var(sigma_amin, nside):
    npix = hp.nside2npix(nside)
    amin_sq_per_pix = 4 * np.pi * (180. * 60. / np.pi) ** 2 / npix
    pixel_var = sigma_amin ** 2 / amin_sq_per_pix
    return pixel_var

def add_noise(sigma_amin, nside):
    sigma_pix = np.sqrt(pixel_var(sigma_amin, nside))
    noise = np.random.randn(3, hp.nside2npix(nside)) * sigma_pix
    return noise

def test_maplike_grid():
    # define true spectral parameters
    beta_s_true = -3.
    beta_d_true = 1.6
    T_d_true = 20.
    nu_ref_s = 23.
    nu_ref_d = 353.
    nside_spec = 2
    nside = 8
    true_params = {
        'beta_d': beta_d_true,
        'T_d': T_d_true,
        'beta_s': beta_s_true,
        'nu_ref_s': nu_ref_s,
        'nu_ref_d': nu_ref_d
    }

    components = ["syncpl", "dustmbb", "cmb"]

    nus = [10., 20., 25., 45., 90., 100., 143., 217., 300., 350., 400., 500.]
    sigmas = [1. * sig for sig in [110., 50., 36., 8., 4, 4, 10.1, 20., 25., 30., 40., 50.]]

    # generate fake synch and dust as GRFs
    ells = np.linspace(0, 3 * nside, 3 * nside + 1)
    cl_s = np.zeros_like(ells)
    cl_d = np.zeros_like(ells)
    cl_s[2:] = 100. * (ells[2:] / 80.) ** - 3.2
    cl_d[2:] = 100. * (ells[2:] / 80.) ** - 3.2

    # the templates of dust and synchrotron at their reference frequencies
    temp_s = np.array(hp.synfast([cl_s, cl_s, cl_s, cl_s], nside, verbose=False, pol=True))
    temp_d = np.array(hp.synfast([cl_d, cl_d, cl_d, cl_d], nside, verbose=False, pol=True))
    temp_c = np.array(hp.synfast([cl_d, cl_d, cl_d, cl_d], nside, verbose=False, pol=True))

    # the synchrotron and dust signals separates
    synch = np.array([temp_s * syncpl(nu, beta_s=beta_s_true, nu_ref_s=nu_ref_s) for nu in nus])
    dust = np.array([temp_d * dustmbb(nu, beta_d=beta_d_true, T_d=T_d_true, nu_ref_d=nu_ref_d) for nu in nus])
    cmbs = np.array([temp_c * cmb(nu) for nu in nus])

    # the noise maps
    noise = [add_noise(sig, nside) for sig in sigmas]

    # these are the simulated observations mean and variance
    # synch + dust + noise
    maps = [d + s + c + n  for d, s, c, n in zip(dust, synch, cmbs, noise)]
    # inverse pixel noise variance
    vars = [np.ones((3, hp.nside2npix(nside))) / pixel_var(sig, nside) for sig in sigmas]

    # Save maps
    test_dir = abspath("test_data")
    fpaths_mean = [join(test_dir, "mean_nu{:03d}.fits".format(int(nu))) for nu in nus]
    fpaths_vars = [join(test_dir, "vars_nu{:03d}.fits".format(int(nu))) for nu in nus]
    for nu, m, fm, v, fv in zip(nus, maps, fpaths_mean, vars, fpaths_vars):
        hp.write_map(fm, m, overwrite=True)
        hp.write_map(fv, v, overwrite=True)

    # start likelihood setup.
    config_dict = {
        "nus": nus,
        "fpaths_mean": fpaths_mean,
        "fpaths_vars": fpaths_vars,
        "nside_spec": nside_spec,
            }

    # initialize sky model and likelihood
    skymodel = SkyModel(components)
    ml = MapLike(config_dict, skymodel)
    gen = ml.split_data()

    # check templates are recovered for true parameters given to maplike
    temp_s_rec = np.zeros_like(temp_s)
    temp_d_rec = np.zeros_like(temp_d)
    temp_c_rec = np.zeros_like(temp_c)
    for (mean, var), ipix_spec in zip(gen, range(hp.nside2npix(ml.nside_spec))):
        print("ipix_spec: ", ipix_spec)
        rec = ml.get_amplitude_mean(mean, var, true_params)
        npix_spec = hp.nside2npix(nside_spec)
        npix_base = hp.nside2npix(nside)
        nsub = int(npix_base / npix_spec)
        inds = hp.nest2ring(nside, range(ipix_spec * nsub, (ipix_spec + 1) * nsub))
        temp_s_rec[:, inds] = rec[:, :, 0]
        temp_d_rec[:, inds] = rec[:, :, 1]
        temp_c_rec[:, inds] = rec[:, :, 2]

    hp.mollview(temp_s[1], title="temp_s input")
    hp.mollview(temp_s_rec[1], title="temp_s recovered")
    hp.mollview(temp_d[1], title="temp_d input")
    hp.mollview(temp_d_rec[1], title="temp_d recovered")
    hp.mollview(temp_d[1], title="temp_c input")
    hp.mollview(temp_d_rec[1], title="temp_c recovered")
    plt.show()

    # compute likelihood on grid of parameters
    gen = ml.split_data()
    params_dicts = []

    # generate grid
    nsamp = 32
    beta_d = np.linspace(-0.1, 0.1, nsamp) + beta_d_true
    T_d = np.linspace(-2, 2, nsamp) + T_d_true
    beta_s = np.linspace(-0.1, 0.1, nsamp) + beta_s_true

    # cycle through data one big pixel at a time
    for (mean, var), ipix_spec in zip(gen, range(hp.nside2npix(ml.nside_spec))):
        print("Calculating likelihood for pixel: ", ipix_spec)
        lkl = np.zeros((nsamp, nsamp, nsamp))
        for i, b_d in enumerate(beta_d):
            for j, T in enumerate(T_d):
                for k, b_s in enumerate(beta_s):
                    params = [b_s, b_d, T]
                    lkl[i, j, k] = ml.marginal_spectral_likelihood(params, mean, var)

        fig, ax = plt.subplots(1, 1)
        ax.set_title("Input frequqency spectrum")
        ax.plot(nus, mean[:, 0, 0])
        plt.show()

        # plot 2d posteriors
        plt.imshow(np.sum(lkl, axis=0), origin='lower', aspect='auto', extent=(T_d.min(), T_d.max(), beta_s.min(), beta_s.max()))
        plt.title("T_d - beta_s")
        plt.xlabel(r"T_d")
        plt.ylabel(r"beta_s")
        plt.colorbar(label=r"$F^T N_T^{-1} F$")
        plt.show()

        plt.imshow(np.sum(lkl, axis=1), origin='lower', aspect='auto', extent=(beta_d.min(), beta_d.max(), beta_s.min(), beta_s.max()))
        plt.title("beta_d - beta_s")
        plt.xlabel(r"beta_d")
        plt.ylabel(r"beta_s")
        plt.colorbar(label=r"$F^T N_T^{-1} F$")
        plt.show()

        plt.imshow(np.sum(lkl, axis=2), origin='lower', aspect='auto', extent=(beta_d.min(), beta_d.max(), T_d.min(), T_d.max()))
        plt.title("beta_d - T_d")
        plt.xlabel(r"beta_d")
        plt.ylabel(r"T_d")
        plt.colorbar(label=r"$F^T N_T^{-1} F$")
        plt.show()

        # plot 1d posteriors
        beta_s_1d = np.sum(lkl, axis=(0, 1))
        T_d_1d = np.sum(lkl, axis=(0, 2))
        beta_d_1d = np.sum(lkl, axis=(1, 2))

        plt.plot(beta_s, beta_s_1d)
        plt.axvline(beta_s_true, color='k', linestyle='--')
        plt.title("beta_s, max={:f}".format(beta_s[np.argmax(beta_s_1d)]))
        plt.show()

        plt.plot(T_d, T_d_1d)
        plt.axvline(T_d_true, color='k', linestyle='--')
        plt.title("T_d, max={:f}".format(T_d[np.argmax(T_d_1d)]))
        plt.show()

        plt.plot(beta_d, beta_d_1d)
        plt.axvline(beta_d_true, color='k', linestyle='--')
        plt.title("beta_d, max={:f}".format(beta_d[np.argmax(beta_d_1d)]))
        plt.show()
    return

if __name__=="__main__":
    test_maplike_grid()
