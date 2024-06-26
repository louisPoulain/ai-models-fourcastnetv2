# (C) Copyright 2023 European Centre for Medium-Range Weather Forecasts.
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.


import logging
import os

import numpy as np
import xarray as xr
import torch
import yaml
from ai_models.model import Model

import ai_models_fourcastnetv2.fourcastnetv2 as nvs

LOG = logging.getLogger(__name__)


class FourCastNetv2(Model):
    # Download
    download_url = "https://get.ecmwf.int/repository/test-data/ai-models/fourcastnetv2/small/{file}"
    download_files = ["weights.tar", "global_means.npy", "global_stds.npy"]

    # Input
    area = [90, 0, -90, 360 - 0.25]
    grid = [0.25, 0.25]

    param_sfc = ["10u", "10v", "2t", "sp", "msl", "tcwv", "100u", "100v"]
    param_sfc_xr = ["u10", "v10", "u100", "v100", "t2m", "sp", "msl", "tcwv"]

    param_level_pl = (
        ["u", "v", "z", "t", "r"],
        [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50],
    )

    ordering_cml = [
        "10u",
        "10v",
        "100u",
        "100v",
        "2t",
        "sp",
        "msl",
        "tcwv",
        "u50",
        "u100",
        "u150",
        "u200",
        "u250",
        "u300",
        "u400",
        "u500",
        "u600",
        "u700",
        "u850",
        "u925",
        "u1000",
        "v50",
        "v100",
        "v150",
        "v200",
        "v250",
        "v300",
        "v400",
        "v500",
        "v600",
        "v700",
        "v850",
        "v925",
        "v1000",
        "z50",
        "z100",
        "z150",
        "z200",
        "z250",
        "z300",
        "z400",
        "z500",
        "z600",
        "z700",
        "z850",
        "z925",
        "z1000",
        "t50",
        "t100",
        "t150",
        "t200",
        "t250",
        "t300",
        "t400",
        "t500",
        "t600",
        "t700",
        "t850",
        "t925",
        "t1000",
        "r50",
        "r100",
        "r150",
        "r200",
        "r250",
        "r300",
        "r400",
        "r500",
        "r600",
        "r700",
        "r850",
        "r925",
        "r1000",
    ]
    
    ordering_xr = [
        "u10",
        "v10",
        "u100",
        "v100",
        "t2m",
        "sp",
        "msl",
        "tcwv",
        "u50",
        "u100",
        "u150",
        "u200",
        "u250",
        "u300",
        "u400",
        "u500",
        "u600",
        "u700",
        "u850",
        "u925",
        "u1000",
        "v50",
        "v100",
        "v150",
        "v200",
        "v250",
        "v300",
        "v400",
        "v500",
        "v600",
        "v700",
        "v850",
        "v925",
        "v1000",
        "z50",
        "z100",
        "z150",
        "z200",
        "z250",
        "z300",
        "z400",
        "z500",
        "z600",
        "z700",
        "z850",
        "z925",
        "z1000",
        "t50",
        "t100",
        "t150",
        "t200",
        "t250",
        "t300",
        "t400",
        "t500",
        "t600",
        "t700",
        "t850",
        "t925",
        "t1000",
        "r50",
        "r100",
        "r150",
        "r200",
        "r250",
        "r300",
        "r400",
        "r500",
        "r600",
        "r700",
        "r850",
        "r925",
        "r1000",
    ]

    # Output
    expver = "sfno"

    def __init__(self, precip_flag=False, **kwargs):
        super().__init__(**kwargs)

        self.n_lat = 721
        self.n_lon = 1440
        self.hour_steps = 6

        self.backbone_channels = len(self.ordering_cml)

        self.checkpoint_path = os.path.join(self.assets, "weights.tar")
        
    def load_statistics(self):
        path = os.path.join(self.assets, "global_means.npy")
        LOG.info("Loading %s", path)
        self.means = np.load(path)
        self.means = self.means[:, : self.backbone_channels, ...]
        self.means = self.means.astype(np.float32)

        path = os.path.join(self.assets, "global_stds.npy")
        LOG.info("Loading %s", path)
        self.stds = np.load(path)
        self.stds = self.stds[:, : self.backbone_channels, ...]
        self.stds = self.stds.astype(np.float32)

    def load_model(self, checkpoint_file):
        model = nvs.FourierNeuralOperatorNet()

        model.zero_grad()
        # Load weights

        checkpoint = torch.load(checkpoint_file, map_location=self.device)

        weights = checkpoint["model_state"]
        drop_vars = ["module.norm.weight", "module.norm.bias"]
        weights = {k: v for k, v in weights.items() if k not in drop_vars}

        # Make sure the parameter names are the same as the checkpoint
        # need to use strict = False to avoid this error message when
        # using sfno_76ch::
        # RuntimeError: Error(s) in loading state_dict for Wrapper:
        # Missing key(s) in state_dict: "module.trans_down.weights",
        # "module.itrans_up.pct",
        try:
            # Try adding model weights as dictionary
            new_state_dict = dict()
            for k, v in checkpoint["model_state"].items():
                name = k[7:]
                if name != "ged":
                    new_state_dict[name] = v
            model.load_state_dict(new_state_dict)
        except Exception:
            model.load_state_dict(checkpoint["model_state"])

        # Set model to eval mode and return
        model.eval()
        model.to(self.device)

        return model

    def normalise(self, data, reverse=False):
        """Normalise data using pre-saved global statistics"""
        if reverse:
            new_data = data * self.stds + self.means
        else:
            new_data = (data - self.means) / self.stds
        return new_data

    def run(self):
        self.load_statistics()

        all_fields = self.all_fields
        
        if isinstance(all_fields, list):
            all_fields_sfc = all_fields[0]
            all_fields_sfc = all_fields_sfc[self.param_sfc_xr].sortby('latitude', ascending=False)
            
            all_fields_pl = all_fields[1]
            params, levels = self.param_level_pl
            all_fields_pl = all_fields_pl.sel(isobaricInhPa=levels)[params].sortby('latitude', ascending=False)
            all_fields_pl_list = []
            for p in self.ordering_xr[8:]:
                param, level = p[0], p[1:]
                all_fields_pl_list.append(all_fields_pl.sel(isobaricInhPa=level)[param])

            all_fields = [all_fields_sfc[f] for f in self.param_sfc_xr] + all_fields_pl_list
            all_fields_numpy = np.concatenate(all_fields)
            
        else:
            all_fields = all_fields.sel(
            param_level=self.ordering_cml, remapping={"param_level": "{param}{levelist}"}
        )
            all_fields = all_fields.order_by(
                {"param_level": self.ordering_cml},
                remapping={"param_level": "{param}{levelist}"},
            )

            all_fields_numpy = all_fields.to_numpy(dtype=np.float32)

        all_fields_numpy = self.normalise(all_fields_numpy)

        model = self.load_model(self.checkpoint_path)

        # Run the inference session
        input_iter = torch.from_numpy(all_fields_numpy).to(self.device)

        # sample_sfc = all_fields.sel(param="2t")[0]
        #if not isinstance(self.all_fields, list):
        #    self.write_input_fields(all_fields)

        torch.set_grad_enabled(False)

        with self.stepper(self.hour_steps) as stepper:
            if isinstance(self.all_fields, list):
                outputs = []
                data_vars = {}
            for i in range(self.lead_time // self.hour_steps):
                output = model(input_iter)

                input_iter = output
                if i == 0 and LOG.isEnabledFor(logging.DEBUG):
                    LOG.debug("Mean/stdev of normalised values: %s", output.shape)

                    for j, name in enumerate(self.ordering):
                        LOG.debug(
                            "    %s %s %s %s %s",
                            name,
                            np.mean(output[:, j].cpu().numpy()),
                            np.std(output[:, j].cpu().numpy()),
                            np.amin(output[:, j].cpu().numpy()),
                            np.amax(output[:, j].cpu().numpy()),
                        )

                # Save the results
                step = (i + 1) * self.hour_steps
                output = self.normalise(output.cpu().numpy(), reverse=True)

                if i == 0 and LOG.isEnabledFor(logging.DEBUG):
                    LOG.debug("Mean/stdev of denormalised values: %s", output.shape)

                    for j, name in enumerate(self.ordering_cml):
                        LOG.debug(
                            "    %s mean=%s std=%s min=%s max=%s",
                            name,
                            np.mean(output[:, j]),
                            np.std(output[:, j]),
                            np.amin(output[:, j]),
                            np.amax(output[:, j]),
                        )
                if not isinstance(self.all_fields, list):
                    for k, fs in enumerate(all_fields):
                        self.write(
                            output[0, k, ...], check_nans=True, template=fs, step=step
                        )
                else:
                    outputs.append(output)

                stepper(i, step)
                
            if isinstance(self.all_fields, list):
                i = 0
                j = 0
                while i<len(self.ordering_xr):
                    if i<8:
                        data_vars[self.ordering_xr[i]] = (
                            ("time", "lat", "lon"),
                            np.array([out[0, i] for out in outputs]),
                        )
                        i += 1
                    else:
                        data_pl = np.empty((len(outputs), 13, 721, 1440))
                        for s, out in enumerate(outputs):
                            for k in range(0, 13):
                                data_pl[s, k] = out[0, i + k]
                        data_pl = np.array(data_pl)
                        data_vars[self.param_level_pl[0][j]] = (
                            ("time", "level", "lat", "lon"),
                            data_pl,
                        )
                        i += 13
                        j += 1
                
                steps = np.arange(6, self.lead_time + 6, 6)    
                times = [self.all_fields[1].time.values[0] + np.timedelta64(steps[i], 'h') for i in range(len(steps))]
                # remove [::-1] for lat as it should be ok now
                lat, lon = self.all_fields[0].latitude.values, self.all_fields[0].longitude.values
                saved_xarray = xr.Dataset(
                    data_vars=data_vars,
                    coords=dict(
                        lon=lon,
                        lat=lat,
                        time=times,
                        level=self.param_level_pl[1][::-1],
                    ),
                )
                saved_xarray = saved_xarray.rename({"level": "isobaricInhPa"})
                start_date = self.all_fields[0].valid_time.values[0]
                
                with open("/work/FAC/FGSE/IDYST/tbeucler/default/raw_data/ML_PREDICT/models_config.yml", "r") as f:
                    folder = yaml.full_load(f).get("fourcastnet_folder")
                name = f"fcnv2_{np.datetime64(start_date, 'h')}_to_{np.datetime64(start_date + np.timedelta64(self.lead_time, 'h'), 'h')}"+\
                    f"_ldt_{self.lead_time}.nc"
                name = os.path.join(folder, name)
                LOG.info(f"Saving to {name}")
                encoding = {}
                for data_var in saved_xarray.data_vars:
                    encoding[data_var] = {
                    "original_shape": saved_xarray[data_var].shape,
                    "_FillValue": -32767,
                    "dtype": np.int16,
                    "add_offset": saved_xarray[data_var].mean().compute().values,
                    "scale_factor": saved_xarray[data_var].std().compute().values / 1000, # save up to 32 std
                    # "zlib": True,
                    # "complevel": 5,
                    }
                saved_xarray.to_netcdf(name, engine="netcdf4", mode="w", encoding=encoding, compute=True)
                #saved_xarray.to_netcdf(name, engine="netcdf4", mode="w", compute=True)

def model(model_version, **kwargs):
    models = {
        "0": FourCastNetv2,
        "small": FourCastNetv2,
        "release": FourCastNetv2,
        "latest": FourCastNetv2,
    }
    return models[model_version](**kwargs)
