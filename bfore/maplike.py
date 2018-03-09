from __future__ import absolute_import, print_function
import numpy as np
import healpy as hp
from copy import deepcopy
from .skymodel import SkyModel
from .instrumentmodel import InstrumentModel
from scipy import stats

class MapLike(object) :
    """
    Map-based likelihood

    NOTES:
        - Implement a chi squared function to take in spectral parameters and
        check the full likelihood for amplitudes
    """
    def __init__(self, config_dict, skymodel):#,instrument_model) :
        """
        Initializes likelihood

        Parameters
        ----------
        config_dict: dictionary
            Dictionary containing all the setup information for the likelihood.
            This contains frequencies of the data, the data mean, the data
            variance, and which spectral parameters to sample.
            Fields this dictionary must have are:
            - fpaths_mean: paths to data means (list(str)).
            - fpaths_vars: paths to data variances (list(str)).
            - var_pars: which parameters to vary (list(str)).
            - nus: frequencies (list(float)).
            - nside_spec: nside at which to vary the spectral parameters (int).

        sky_model: SkyModel
            SkyModel object describing all sky components, contains the
            SkyModel.fnu method, which is used to calculate the SED.
        instrument_model: InstrumentModel
            InstrumentModel object describing the instrument's response to the
            sky.
        """
        self.sky = skymodel
        self.__dict__.update(config_dict)
        self.check_parameters()
        #self.inst = instrument_model
        self.read_data()
        #Here we could precompute the F matrix and interpolate it over spectral parameters.

    def check_parameters(self):
        """ Method to check that all the parameters required by the skymodel
        have been specified as either fixed, with a specific value, or are
        desginated as variable.

        Raises
        ------
        ConfigError
        """
        if any([par in self.fixed_pars for par in self.var_pars]):
            print("Check parameter not in both fixed and variable parameters.")
            exit()
        # get parameters required by SkyModel
        model_pars = set(self.sky.get_param_names())
        # get fixed parameter names, specified in the config
        config_pars = set(self.fixed_pars)
        # update this with the variable parameter names, specified in the config
        config_pars.update(set(self.var_pars))
        # check these sets are the same
        if not (model_pars == config_pars):
            print("Parameter mismatch between model and MapLike configuration")
            exit()
        return

    def read_data(self):
        """ Method to read input data. The `self.data_mean` and `self.data_vars`
        will have shape: (N_freqs, N_pol, N_pix)
        """
        self.data_mean = read_hpix_maps(self.fpaths_mean, nest=False)
        self.data_vars = read_hpix_maps(self.fpaths_vars, nest=False)
        self.nside_base = hp.get_nside(self.data_mean[0][0])
        return

    def split_data(self, ipix=None):
        """ Generator function to return the data each task is to work on. This
        is one large spectral index pixel, and the corresponding pixels at the
        base amplitude resolution.

        Parameters
        ----------
        ipix: int, list(int)
            If passed this parameter instructs which pixels to return,
            defined in the nested indexing scheme.  This may be a single
            pixel, or a list of pixels, which will be returned as a
            generator. If this parameter is not passed, all pixels will
            be returned in the form of a generator
            (optional, default=None).

        Returns
        -------
        generator
            Generator function that yields a tuple containing the data mean
            and variance within one large pixel over which the spectral
            paraemters are held constant.
        """
        npix_spec = hp.nside2npix(self.nside_spec)
        npix_base = hp.nside2npix(self.nside_base)
        nside_sub = int(npix_base / npix_spec)

        if ipix is not None:
            if isinstance(ipix, int):
                ipixs = [ipix]
            if isinstance(ipix, list):
                ipixs = ipix
        else:
            ipixs = range(npix_spec)

        for i in ipixs:
            inds = hp.nest2ring(self.nside_base, range(i * nside_sub, (i + 1) * nside_sub))
            mean = self.data_mean[:, :, inds]
            vars = self.data_vars[:, :, inds]
            yield (mean, vars)

    def f_matrix(self, var_pars_list, inst_params=None) :
        """
        Returns the instrument's response to each of the sky components in each
        frequency channel.

        Parameters
        ----------
        var_pars_list: list
            Parameters necessary to describe all components in the sky model
        inst_params: dictf(x, df, loc=0,
            Parameters describing the instrument (none needed/implemented yet).

        Returns::
        -------
        array_like(float)
            The returned array has shape (N_pol, N_comp, N_freq).
        """
        # put the list of parameter values into a dictionary
        spec_params = {par_name:par_val for par_name, par_val in zip(self.var_pars, var_pars_list)}
        # add the parameters that are fixed
        spec_params.update(self.fixed_pars)
        #return self.inst.convolve_sed(self.sky.fnu,args=spec_params,instpar=inst_params)
        return self.sky.fnu(self.nus, spec_params)

    def get_amplitude_covariance(self, n_ivar_map, spec_params,
                                    inst_params=None, f_matrix=None):
        """
        Computes the covariance of the different component amplitudes.

        Parameters
        ----------
        n_ivar_map: array_like(float)
            2D array with dimensions (N_freq, N_pol, N_pix), where N_pol is the
            number of polarization channels and N_pix is the number of pixels.
            Each element of this array should contain the inverse noise variance
            in that pixel and frequency channel. Uncorrelated noise is assumed.
        spec_params: dict
            Parameters necessary to describe all components in the sky model
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).
        f_matrix: array_like(float)
            Array with shape (N_comp, N_freq) (see f_matrix above). If not None,
            the F matrix won't be recalculated.

        Returns
        -------
        array_like(float)
            Array with dimensions (N_pol,N_pix,N_comp,N_comp), containing the
            noise covariance of all component amplitudes in each pixel and
            polarization channel.
        """
        if f_matrix is None:
            f_matrix = self.f_matrix(spec_params, inst_params)
        # (N_comp, N_pol, N_freq) x (N_comp, N_pol, N_freq) = (N_pol, N_comp, N_comp, N_freq)
        f_mat_outer = np.einsum("ijk,ljk->jilk", f_matrix, f_matrix)
        # (N_pol, N_comp, N_comp, N_freq) * (N_freq, N_pol, N_pix) = (N_pol, N_pix, N_comp, N_comp)
        amp_covar_inv = np.einsum("ijkl,lin->injk", f_mat_outer, n_ivar_map)
        return amp_covar_inv

    def get_amplitude_mean(self, d_map, n_ivar_map, spec_params,
                            inst_params=None, f_matrix=None, nt_inv_matrix=None):
        """
        Computes the best-fit amplitudes for all components.

        Parameters
        ----------
        d_map: array_like(float)
            array with dimensions (N_freq, N_pol, N_pix), where N_pol is the number of
            polarization channels and N_pix is the number of pixels. Each
            element of this array should contain the measured
            temperature/polarization in that pixel and frequency channel.
        n_ivar_map: array_like(float)
            array with dimensions (N_freq, N_pol, N_pix), where N_pol is the
            number of polarization channels and N_pix is the number of pixels.
            Each element of this array should contain the inverse noise variance
            in that pixel and frequency channel. Uncorrelated noise is assumed.
        spec_params: dict
            Parameters necessary to describe all components in the sky model
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).
        f_matrix: array_like(float)
            Array with shape (N_comp, N_freq) (see f_matrix above). If not None,
            the F matrix won't be recalculated.
        nt_matrix: array_like(float)
            Array with shape (N_pol, N_pix, N_comp, N_comp) (see
            `get_amplitude_covariance` above). If not None, the N_T matrix won't
            be recalculated.

        Returns
        -------
        array_like
            Array with dimensions (N_pol, N_pix, N_comp).
        """
        # Again, we're allowing F and N_T to be passed to avoid extra operations.
        # Should we be passing choleskys here?
        if f_matrix is None:
            f_matrix = self.f_matrix(spec_params, inst_params)
        if nt_inv_matrix is None:
            nt_inv_matrix = self.get_amplitude_covariance(n_ivar_map,
                spec_params, inst_params=inst_params, f_matrix=f_matrix)
        # NOTE: n_ivar_map * d_map should not be calculated for each iteration
        # (N_comp, N_pol, N_freq) * (N_freq, N_pol, N_pix) * (N_freq, N_pol, N_pix) = (N_pol, N_pix, N_comp)
        y = np.einsum("jik,kil,kil->ilj", f_matrix, n_ivar_map, d_map)
        # Get the solution to: N_T_inv T_bar = F^T N^-1 d
        amp_mean = np.linalg.solve(nt_inv_matrix, y)
        return amp_mean

    def marginal_spectral_likelihood(self, spec_params, d_map, n_ivar_map,
                                        inst_params=None, volume_prior=True,
                                        lnprior=None):
        """ Function to calculate the likelihood marginalized over amplitude
        parameters.

        Parameters
        ----------
        d_map, n_ivar_map: array_like(float)
            Subset of input data pixel mean and pixel variance respectively.
            Only contains pixels within the large pixel over which spectral
            parameters are constant. Shape (Nfreq, Npol, Npix) where
            Npix = (Nside_small / Nside_big) ** 2.
        spec_params: list
            List of the variable parameters that will be sampled. These must be
            passed in the order of the list self.var_pars.
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).

        Returns
        -------
        float
            Likelihood at this point in parameter space.
        """
        # calculate sed for proposal spectral parameters
        f_matrix = self.f_matrix(spec_params, inst_params=inst_params)
        # get amplitude covariance for proposal spectral parameters
        amp_covar_matrix = self.get_amplitude_covariance(n_ivar_map,
                                            spec_params, inst_params, f_matrix)
        # get amplitude mean for proposal spectral parameters
        amp_mean = self.get_amplitude_mean(d_map, n_ivar_map, spec_params,
                                            inst_params, f_matrix=f_matrix,
                                            nt_inv_matrix=amp_covar_matrix)
        return np.einsum("ijk,ijkl,ijl->", amp_mean, amp_covar_matrix, amp_mean)

    def chi2(self, spec_params, d_map, n_ivar_map, inst_params=None,
                f_matrix=None, volume_prior=True, lnprior=None):
        """ Function to calculate the chi2 of a given set of spectral
        parameters.

        This function first computes the mean amplitude templates, and then uses
        these in the unmarginalized likelihood to compute the chi2 defined by:

        ..math::
            \chi^2 = (d-FT)^T N^{-1}(d-FT)

        Parameters
        ----------
        d_map, n_ivar_map: array_like(float)
            Subset of input data pixel mean and pixel variance respectively.
            Only contains pixels within the large pixel over which spectral
            parameters are constant. Shape (Nfreq, Npol, Npix) where
            Npix = (Nside_small / Nside_big) ** 2.
        spec_params: list
            List of the variable parameters that will be sampled. These must be
            passed in the order of the list self.var_pars.
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).

        Returns
        -------
        float
            Chi squared for given spectral parameters.
        """
        if f_matrix is None:
            f_matrix = self.f_matrix(spec_params, inst_params)

        amp_mean = self.get_amplitude_mean(d_map, n_ivar_map, spec_params,
                                            inst_params=None, f_matrix=None,
                                            nt_inv_matrix=None)
        #             (N_comp, N_pol, N_freq) * (N_pol, N_pix, N_comp) = (N_freq, N_pol, N_pix)
        res = d_map - np.einsum("ijk,jli->kjl", f_matrix, amp_mean)
        dof = len(d_map.flatten()) - len(self.var_pars) - len(amp_mean.flatten())

        chi2 = np.einsum("ijk,ijk,ijk->", res, n_ivar_map, res)
        return chi2

    def chi2perdof(self, spec_params, d_map, n_ivar_map, inst_params=None,
                f_matrix=None, volume_prior=True, lnprior=None):
        """ Function to calculate the reduced chi2 of a given set of spectral
        parameters.

        This function first computes the mean amplitude templates, and then uses
        these in the unmarginalized likelihood to compute the chi2 defined by:

        ..math::
            \chi^2 = (d-FT)^T N^{-1}(d-FT) / {\rm dof}

        Parameters
        ----------
        d_map, n_ivar_map: array_like(float)
            Subset of input data pixel mean and pixel variance respectively.
            Only contains pixels within the large pixel over which spectral
            parameters are constant. Shape (Nfreq, Npol, Npix) where
            Npix = (Nside_small / Nside_big) ** 2.
        spec_params: list
            List of the variable parameters that will be sampled. These must be
            passed in the order of the list self.var_pars.
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).

        Returns
        -------
        float
            Chi squared per degree of freedom for given spectral parameters.
        """
        chi2 = self.chi2(spec_params, d_map, n_ivar_map, inst_params=None,
                    f_matrix=f_matrix, volume_prior=volume_prior, lnprior=lnprior)
        dof = len(d_map.flatten()) - len(self.var_pars) - len(amp_mean.flatten())
        return chi2 / float(dof)

    def pval(self, spec_params, d_map, n_ivar_map, inst_params=None,
                f_matrix=None, volume_prior=True, lnprior=None):
        """ Function to calculate the p-value of a given set of spectral
        parameters.

        Parameters
        ----------
        d_map, n_ivar_map: array_like(float)
            Subset of input data pixel mean and pixel variance respectively.
            Only contains pixels within the large pixel over which spectral
            parameters are constant. Shape (Nfreq, Npol, Npix) where
            Npix = (Nside_small / Nside_big) ** 2.
        spec_params: list
            List of the variable parameters that will be sampled. These must be
            passed in the order of the list self.var_pars.
        inst_params: dict
            Parameters describing the instrument (none needed/implemented yet).

        Returns
        -------
        float
            p-value for given spectral parameters.
        """
        chi2 = self.chi2(spec_params, d_map, n_ivar_map, inst_params=None,
                    f_matrix=f_matrix, volume_prior=volume_prior, lnprior=lnprior)
        dof = len(d_map.flatten()) - len(self.var_pars) - len(amp_mean.flatten())
        return 1. - stats.chi2.cdf(chi2, dof)


def read_hpix_maps(fpaths, verbose=False, *args, **kwargs):
    """ Convenience function for reading in a list of paths to healpix maps and
    returning an array of the maps.

    Parameters
    ----------
    fpaths: list(str)
        List of paths to healpix maps.

    Returns
    -------
    array_like(floats)
        Array of shape (len(`fpaths`), npix) for just T maps, or
        (len(`fpaths`), 3, npix) for TQU maps.

    NOTE:
        - Add option for choosing polarization or not.
    """
    gen = (hp.read_map(fpath, verbose=verbose, field=(0, 1, 2), **kwargs) for fpath in fpaths)
    return np.array(list(gen))
