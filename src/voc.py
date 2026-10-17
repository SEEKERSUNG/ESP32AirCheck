# -*- coding: utf-8 -*-
"""
Sensirion VOC 算法 (VOC Index) —— MicroPython 移植版
================================================================
把 SGP40 的原始信号 SRAW_VOC 转换成国际通用的 VOC 指数(0~500)。

算法来源: Sensirion gas-index-algorithm (BSD-3-Clause)
    sensirion_gas_index_algorithm.c / .h
    本文件是它的浮点版本逐行移植, 数值与官方 C 实现一致。

VOC 指数的含义 (Sensirion 官方定义):
    100   = 当前环境的平均基线(典型室内空气)
    >100  = 有机物浓度高于基线, 数值越大越差
    <100  = 比基线更干净
    0     = 前 45 秒的初始 blackout 阶段(算法还没开始出数)
    500   = 上限

同时算法还内置了:
    * 前 45s 的 blackout
    * 均值/方差的自适应估计(会随环境慢慢学习基线, 时间常数 12 小时)
    * 自适应低通滤波, 抑制毛刺
    * gating: 浓度突然飙升时冻结基线学习, 避免把污染"学成"正常

采样间隔非常重要: 默认必须每 1 秒喂一次, 项目里也是按 1s 采样的。
"""

import math

_ALGORITHM_TYPE_VOC = 0
_ALGORITHM_TYPE_NOX = 1

_DEFAULT_SAMPLING_INTERVAL = 1.0
_INITIAL_BLACKOUT = 45.0
_INDEX_GAIN = 230.0
_SRAW_STD_INITIAL = 50.0
_SRAW_STD_BONUS_VOC = 220.0
_SRAW_STD_NOX = 2000.0
_TAU_MEAN_HOURS = 12.0
_TAU_VARIANCE_HOURS = 12.0
_TAU_INITIAL_MEAN_VOC = 20.0
_TAU_INITIAL_MEAN_NOX = 1200.0
_INIT_DURATION_MEAN_VOC = 3600.0 * 0.75
_INIT_DURATION_MEAN_NOX = 3600.0 * 4.75
_INIT_TRANSITION_MEAN = 0.01
_TAU_INITIAL_VARIANCE = 2500.0
_INIT_DURATION_VARIANCE_VOC = 3600.0 * 1.45
_INIT_DURATION_VARIANCE_NOX = 3600.0 * 5.70
_INIT_TRANSITION_VARIANCE = 0.01
_GATING_THRESHOLD_VOC = 340.0
_GATING_THRESHOLD_NOX = 30.0
_GATING_THRESHOLD_INITIAL = 510.0
_GATING_THRESHOLD_TRANSITION = 0.09
_GATING_VOC_MAX_DURATION_MINUTES = 60.0 * 3.0
_GATING_NOX_MAX_DURATION_MINUTES = 60.0 * 12.0
_GATING_MAX_RATIO = 0.3
_SIGMOID_L = 500.0
_SIGMOID_K_VOC = -0.0065
_SIGMOID_X0_VOC = 213.0
_SIGMOID_K_NOX = -0.0101
_SIGMOID_X0_NOX = 614.0
_VOC_INDEX_OFFSET_DEFAULT = 100.0
_NOX_INDEX_OFFSET_DEFAULT = 1.0
_LP_TAU_FAST = 20.0
_LP_TAU_SLOW = 500.0
_LP_ALPHA = -0.2
_VOC_SRAW_MINIMUM = 20000
_NOX_SRAW_MINIMUM = 10000
_PERSISTENCE_UPTIME_GAMMA = 3.0 * 3600.0
_MVE_GAMMA_SCALING = 64.0
_MVE_ADDITIONAL_GAMMA_MEAN_SCALING = 8.0
_MVE_FIX16_MAX = 32767.0


class VocAlgorithm:
    """SGP40 原始信号 -> VOC 指数。"""

    def __init__(self, sampling_interval=1.0, algorithm_type="voc"):
        self._type = (_ALGORITHM_TYPE_NOX if algorithm_type == "nox"
                      else _ALGORITHM_TYPE_VOC)
        self.mSamplingInterval = float(sampling_interval)
        self.init()

    # ------------------------------------------------------------------
    def init(self):
        p = self
        if p._type == _ALGORITHM_TYPE_NOX:
            p.mIndex_Offset = _NOX_INDEX_OFFSET_DEFAULT
            p.mSraw_Minimum = _NOX_SRAW_MINIMUM
            p.mGating_Max_Duration_Minutes = _GATING_NOX_MAX_DURATION_MINUTES
            p.mInit_Duration_Mean = _INIT_DURATION_MEAN_NOX
            p.mInit_Duration_Variance = _INIT_DURATION_VARIANCE_NOX
            p.mGating_Threshold = _GATING_THRESHOLD_NOX
        else:
            p.mIndex_Offset = _VOC_INDEX_OFFSET_DEFAULT
            p.mSraw_Minimum = _VOC_SRAW_MINIMUM
            p.mGating_Max_Duration_Minutes = _GATING_VOC_MAX_DURATION_MINUTES
            p.mInit_Duration_Mean = _INIT_DURATION_MEAN_VOC
            p.mInit_Duration_Variance = _INIT_DURATION_VARIANCE_VOC
            p.mGating_Threshold = _GATING_THRESHOLD_VOC
        p.mIndex_Gain = _INDEX_GAIN
        p.mTau_Mean_Hours = _TAU_MEAN_HOURS
        p.mTau_Variance_Hours = _TAU_VARIANCE_HOURS
        p.mSraw_Std_Initial = _SRAW_STD_INITIAL
        p.reset()

    def reset(self):
        self.mUptime = 0.0
        self.mSraw = 0.0
        self.mVoc_Index = 0.0
        self._init_instances()

    # ------------------------------------------------------------------
    def _init_instances(self):
        p = self
        # --- mean variance estimator 参数 ---
        p._mve_Initialized = False
        p._mve_Mean = 0.0
        p._mve_Sraw_Offset = 0.0
        p._mve_Std = p.mSraw_Std_Initial
        dt = p.mSamplingInterval / 3600.0
        p._mve_Gamma_Mean = (
            (_MVE_ADDITIONAL_GAMMA_MEAN_SCALING * _MVE_GAMMA_SCALING * dt)
            / (p.mTau_Mean_Hours + dt))
        p._mve_Gamma_Variance = (
            (_MVE_GAMMA_SCALING * dt) / (p.mTau_Variance_Hours + dt))
        if p._type == _ALGORITHM_TYPE_NOX:
            p._mve_Gamma_Initial_Mean = (
                (_MVE_ADDITIONAL_GAMMA_MEAN_SCALING * _MVE_GAMMA_SCALING
                 * p.mSamplingInterval)
                / (_TAU_INITIAL_MEAN_NOX + p.mSamplingInterval))
        else:
            p._mve_Gamma_Initial_Mean = (
                (_MVE_ADDITIONAL_GAMMA_MEAN_SCALING * _MVE_GAMMA_SCALING
                 * p.mSamplingInterval)
                / (_TAU_INITIAL_MEAN_VOC + p.mSamplingInterval))
        p._mve_Gamma_Initial_Variance = (
            (_MVE_GAMMA_SCALING * p.mSamplingInterval)
            / (_TAU_INITIAL_VARIANCE + p.mSamplingInterval))
        p._mve_Gamma_Mean_used = 0.0
        p._mve_Gamma_Variance_used = 0.0
        p._mve_Uptime_Gamma = 0.0
        p._mve_Uptime_Gating = 0.0
        p._mve_Gating_Duration_Minutes = 0.0
        p._mve_Sigmoid_K = 0.0
        p._mve_Sigmoid_X0 = 0.0

        # --- mox model ---
        p._mox_Sraw_Std = self._mve_get_std()
        p._mox_Sraw_Mean = self._mve_get_mean()

        # --- sigmoid scaled ---
        if p._type == _ALGORITHM_TYPE_NOX:
            p._sig_K = _SIGMOID_K_NOX
            p._sig_X0 = _SIGMOID_X0_NOX
            p._sig_Offset_Default = _NOX_INDEX_OFFSET_DEFAULT
        else:
            p._sig_K = _SIGMOID_K_VOC
            p._sig_X0 = _SIGMOID_X0_VOC
            p._sig_Offset_Default = _VOC_INDEX_OFFSET_DEFAULT

        # --- adaptive lowpass ---
        p._lp_A1 = p.mSamplingInterval / (_LP_TAU_FAST + p.mSamplingInterval)
        p._lp_A2 = p.mSamplingInterval / (_LP_TAU_SLOW + p.mSamplingInterval)
        p._lp_Initialized = False
        p._lp_X1 = 0.0
        p._lp_X2 = 0.0
        p._lp_X3 = 0.0

    def set_tuning_parameters(self, index_offset=100, learning_time_offset_hours=12,
                              learning_time_gain_hours=12,
                              gating_max_duration_minutes=180,
                              std_initial=50, gain_factor=230):
        self.mIndex_Offset = float(index_offset)
        self.mTau_Mean_Hours = float(learning_time_offset_hours)
        self.mTau_Variance_Hours = float(learning_time_gain_hours)
        self.mGating_Max_Duration_Minutes = float(gating_max_duration_minutes)
        self.mSraw_Std_Initial = float(std_initial)
        self.mIndex_Gain = float(gain_factor)
        self._init_instances()

    # ------------------------------------------------------------------
    # mean variance estimator
    # ------------------------------------------------------------------
    def _mve_get_std(self):
        return self._mve_Std

    def _mve_get_mean(self):
        return self._mve_Mean + self._mve_Sraw_Offset

    def _mve_is_initialized(self):
        return self._mve_Initialized

    @staticmethod
    def _sigmoid(k, x0, sample):
        x = k * (sample - x0)
        if x < -50.0:
            return 1.0
        if x > 50.0:
            return 0.0
        return 1.0 / (1.0 + math.exp(x))

    def _mve_sigmoid(self, x0, k, sample):
        return self._sigmoid(k, x0, sample)

    def _mve_calculate_gamma(self):
        p = self
        uptime_limit = _MVE_FIX16_MAX - p.mSamplingInterval
        if p._mve_Uptime_Gamma < uptime_limit:
            p._mve_Uptime_Gamma += p.mSamplingInterval
        if p._mve_Uptime_Gating < uptime_limit:
            p._mve_Uptime_Gating += p.mSamplingInterval

        sigmoid_gamma_mean = p._mve_sigmoid(
            p.mInit_Duration_Mean, _INIT_TRANSITION_MEAN, p._mve_Uptime_Gamma)
        gamma_mean = p._mve_Gamma_Mean + (
            (p._mve_Gamma_Initial_Mean - p._mve_Gamma_Mean) * sigmoid_gamma_mean)

        gating_threshold_mean = p.mGating_Threshold + (
            (_GATING_THRESHOLD_INITIAL - p.mGating_Threshold)
            * p._mve_sigmoid(p.mInit_Duration_Mean, _INIT_TRANSITION_MEAN,
                             p._mve_Uptime_Gating))
        sigmoid_gating_mean = p._mve_sigmoid(
            gating_threshold_mean, _GATING_THRESHOLD_TRANSITION, p.mVoc_Index)
        p._mve_Gamma_Mean_used = sigmoid_gating_mean * gamma_mean

        sigmoid_gamma_variance = p._mve_sigmoid(
            p.mInit_Duration_Variance, _INIT_TRANSITION_VARIANCE,
            p._mve_Uptime_Gamma)
        gamma_variance = p._mve_Gamma_Variance + (
            (p._mve_Gamma_Initial_Variance - p._mve_Gamma_Variance)
            * (sigmoid_gamma_variance - sigmoid_gamma_mean))

        gating_threshold_variance = p.mGating_Threshold + (
            (_GATING_THRESHOLD_INITIAL - p.mGating_Threshold)
            * p._mve_sigmoid(p.mInit_Duration_Variance,
                             _INIT_TRANSITION_VARIANCE, p._mve_Uptime_Gating))
        sigmoid_gating_variance = p._mve_sigmoid(
            gating_threshold_variance, _GATING_THRESHOLD_TRANSITION,
            p.mVoc_Index)
        p._mve_Gamma_Variance_used = sigmoid_gating_variance * gamma_variance

        p._mve_Gating_Duration_Minutes += (
            (p.mSamplingInterval / 60.0)
            * (((1.0 - sigmoid_gating_mean) * (1.0 + _GATING_MAX_RATIO))
               - _GATING_MAX_RATIO))
        if p._mve_Gating_Duration_Minutes < 0.0:
            p._mve_Gating_Duration_Minutes = 0.0
        if p._mve_Gating_Duration_Minutes > p.mGating_Max_Duration_Minutes:
            p._mve_Uptime_Gating = 0.0

    def _mve_process(self, sraw):
        p = self
        if not p._mve_Initialized:
            p._mve_Initialized = True
            p._mve_Sraw_Offset = sraw
            p._mve_Mean = 0.0
            return

        if p._mve_Mean >= 100.0 or p._mve_Mean <= -100.0:
            p._mve_Sraw_Offset += p._mve_Mean
            p._mve_Mean = 0.0
        sraw = sraw - p._mve_Sraw_Offset

        p._mve_calculate_gamma()

        delta_sgp = (sraw - p._mve_Mean) / _MVE_GAMMA_SCALING
        if delta_sgp < 0.0:
            c = p._mve_Std - delta_sgp
        else:
            c = p._mve_Std + delta_sgp

        additional_scaling = 1.0
        if c > 1440.0:
            additional_scaling = (c / 1440.0) * (c / 1440.0)

        p._mve_Std = (
            math.sqrt(additional_scaling
                      * (_MVE_GAMMA_SCALING - p._mve_Gamma_Variance_used))
            * math.sqrt(
                (p._mve_Std * (p._mve_Std
                               / (_MVE_GAMMA_SCALING * additional_scaling)))
                + (((p._mve_Gamma_Variance_used * delta_sgp)
                    / additional_scaling) * delta_sgp)))
        p._mve_Mean = p._mve_Mean + (
            (p._mve_Gamma_Mean_used * delta_sgp)
            / _MVE_ADDITIONAL_GAMMA_MEAN_SCALING)

    # ------------------------------------------------------------------
    # mox model
    # ------------------------------------------------------------------
    def _mox_process(self, sraw):
        p = self
        if p._type == _ALGORITHM_TYPE_NOX:
            return (((sraw - p._mox_Sraw_Mean) / _SRAW_STD_NOX) * p.mIndex_Gain)
        return (((sraw - p._mox_Sraw_Mean)
                 / (-1.0 * (p._mox_Sraw_Std + _SRAW_STD_BONUS_VOC)))
                * p.mIndex_Gain)

    # ------------------------------------------------------------------
    # sigmoid scaled
    # ------------------------------------------------------------------
    def _sigmoid_scaled_process(self, sample):
        p = self
        x = p._sig_K * (sample - p._sig_X0)
        if x < -50.0:
            return _SIGMOID_L
        if x > 50.0:
            return 0.0
        if sample >= 0.0:
            if p._sig_Offset_Default == 1.0:
                shift = (500.0 / 499.0) * (1.0 - p.mIndex_Offset)
            else:
                shift = (_SIGMOID_L - (5.0 * p.mIndex_Offset)) / 4.0
            return ((_SIGMOID_L + shift) / (1.0 + math.exp(x))) - shift
        return ((p.mIndex_Offset / p._sig_Offset_Default)
                * (_SIGMOID_L / (1.0 + math.exp(x))))

    # ------------------------------------------------------------------
    # adaptive lowpass
    # ------------------------------------------------------------------
    def _lp_process(self, sample):
        p = self
        if not p._lp_Initialized:
            p._lp_X1 = sample
            p._lp_X2 = sample
            p._lp_X3 = sample
            p._lp_Initialized = True

        p._lp_X1 = ((1.0 - p._lp_A1) * p._lp_X1) + (p._lp_A1 * sample)
        p._lp_X2 = ((1.0 - p._lp_A2) * p._lp_X2) + (p._lp_A2 * sample)
        abs_delta = p._lp_X1 - p._lp_X2
        if abs_delta < 0.0:
            abs_delta = -abs_delta
        f1 = math.exp(_LP_ALPHA * abs_delta)
        tau_a = ((_LP_TAU_SLOW - _LP_TAU_FAST) * f1) + _LP_TAU_FAST
        a3 = p.mSamplingInterval / (p.mSamplingInterval + tau_a)
        p._lp_X3 = ((1.0 - a3) * p._lp_X3) + (a3 * sample)
        return p._lp_X3

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def process(self, sraw):
        """
        喂入一个 SGP40 原始值, 返回 VOC 指数(整数)。
        * 前 45 秒返回 0 (blackout)
        * 之后返回 1~500
        """
        p = self
        if p.mUptime <= _INITIAL_BLACKOUT:
            p.mUptime += p.mSamplingInterval
        else:
            if 0 < sraw < 65000:
                if sraw < p.mSraw_Minimum + 1:
                    sraw = p.mSraw_Minimum + 1
                elif sraw > p.mSraw_Minimum + 32767:
                    sraw = p.mSraw_Minimum + 32767
                p.mSraw = float(sraw - p.mSraw_Minimum)

            if p._type == _ALGORITHM_TYPE_VOC or p._mve_is_initialized():
                p.mVoc_Index = p._mox_process(p.mSraw)
                p.mVoc_Index = p._sigmoid_scaled_process(p.mVoc_Index)
            else:
                p.mVoc_Index = p.mIndex_Offset

            p.mVoc_Index = p._lp_process(p.mVoc_Index)
            if p.mVoc_Index < 0.5:
                p.mVoc_Index = 0.5
            if p.mSraw > 0.0:
                p._mve_process(p.mSraw)
                p._mox_Sraw_Std = p._mve_get_std()
                p._mox_Sraw_Mean = p._mve_get_mean()

        return int(p.mVoc_Index + 0.5)
