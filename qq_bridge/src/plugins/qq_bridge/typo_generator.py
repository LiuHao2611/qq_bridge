# -*- coding: utf-8 -*-
"""typo_generator —— 中文错别字生成器（2026-10-19 搬运自 MaiMBot）。

来源：MaiMBot（Ruins996/SengokuCola，github.com/Ruins996/MaiMBot）
      src/plugins/utils/typo_generator.py —— 算法逻辑**逐行忠实搬运**，
      仅做本地兼容适配（下方 ADAPTATION 标注），不改行为：

  ADAPTATION 1（本地兼容）：字频缓存 char_frequency.json 从 CWD 改到
     项目 data/ 目录（运行时数据必须落项目根，禁散落）；
  ADAPTATION 2（本地兼容）：`_get_word_homophones` 原版每次调用都重读
     jieba dict.txt（5MB，每词一次 → 单回复可卡数秒），改为懒加载缓存，
     行为不变、只读一次；
  ADAPTATION 3（本地兼容）：print 改项目 logger（DEBUG 级），避免刷屏。

原版用法（MaiMBot utils.process_llm_response）：
    ChineseTypoGenerator(error_rate=0.03, min_freq=7,
                         tone_error_rate=0.2, word_replace_rate=0.02)
即 3% 单字同音替换、20% 声调错误倾向、2% 整词同音词替换——
麦麦 README 主打「随机生成错别字」的真身。
"""
from __future__ import annotations

import itertools
import json
import math
import os
import random
from collections import defaultdict
from pathlib import Path

import jieba
from pypinyin import Style, pinyin

from .config import get_logger, jlog

log = get_logger()

# ADAPTATION 1：缓存落项目 data/（qq_bridge/data/char_frequency.json）
_FREQ_CACHE = Path(__file__).resolve().parents[3] / "data" / "char_frequency.json"


class ChineseTypoGenerator:
    """基于拼音和字频的中文错别字生成工具（搬运版）。"""

    def __init__(self,
                 error_rate=0.3,
                 min_freq=5,
                 tone_error_rate=0.2,
                 word_replace_rate=0.3,
                 max_freq_diff=200):
        """
        参数:
            error_rate: 单字替换概率
            min_freq: 最小字频阈值
            tone_error_rate: 声调错误概率
            word_replace_rate: 整词替换概率
            max_freq_diff: 最大允许的频率差异
        """
        self.error_rate = error_rate
        self.min_freq = min_freq
        self.tone_error_rate = tone_error_rate
        self.word_replace_rate = word_replace_rate
        self.max_freq_diff = max_freq_diff

        # 加载数据
        self.pinyin_dict = self._create_pinyin_dict()
        self.char_frequency = self._load_or_create_char_frequency()
        # ADAPTATION 2：jieba 词典懒加载缓存（原版每词重读 5MB）
        self._valid_words: dict[str, float] | None = None

    def _load_or_create_char_frequency(self):
        """加载或创建汉字频率字典（缓存文件在项目 data/）。"""
        cache_file = _FREQ_CACHE

        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001 — 坏缓存则重建
                pass

        # 使用内置的词频文件（jieba dict.txt）
        char_freq = defaultdict(int)
        dict_path = os.path.join(os.path.dirname(jieba.__file__), 'dict.txt')

        try:
            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    word, freq = parts[0], parts[1]
                    try:
                        freq_i = int(freq)
                    except ValueError:
                        continue
                    # 对词中的每个字进行频率累加
                    for char in word:
                        if self._is_chinese_char(char):
                            char_freq[char] += freq_i
        except Exception as e:  # noqa: BLE001 — 词典读取失败用空频表兜底
            log.warning(jlog("typo_freq_build_failed", error=type(e).__name__))
            return {char: 1.0 for char in
                    (chr(i) for i in range(0x4e00, 0x9fa5))}

        if not char_freq:
            return {}

        # 归一化频率值
        max_freq = max(char_freq.values())
        normalized = {char: freq / max_freq * 1000 for char, freq in char_freq.items()}

        # 保存到缓存
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(normalized, f, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — 缓存写失败不影响运行
            log.warning(jlog("typo_freq_cache_failed", error=type(e).__name__))
        return normalized

    def _create_pinyin_dict(self):
        """创建拼音到汉字的映射字典（常用汉字范围）。"""
        # 常用汉字范围
        chars = [chr(i) for i in range(0x4e00, 0x9fff)]
        pinyin_dict = defaultdict(list)

        for char in chars:
            try:
                py = pinyin(char, style=Style.TONE3)[0][0]
                pinyin_dict[py].append(char)
            except Exception:  # noqa: BLE001
                continue

        return pinyin_dict

    def _is_chinese_char(self, char):
        """判断是否为汉字。"""
        try:
            return '\u4e00' <= char <= '\u9fff'
        except Exception:  # noqa: BLE001
            return False

    def _get_pinyin(self, sentence):
        """将中文句子拆分成单个汉字并获取其拼音。"""
        characters = list(sentence)
        result = []
        for char in characters:
            if char.isspace() or not self._is_chinese_char(char):
                continue
            py = pinyin(char, style=Style.TONE3)[0][0]
            result.append((char, py))
        return result

    def _get_similar_tone_pinyin(self, py):
        """获取相似声调的拼音。"""
        if not py or len(py) < 1:
            return py

        if not py[-1].isdigit():
            # 为非数字结尾的拼音添加数字声调1
            return py + '1'

        base = py[:-1]
        tone = int(py[-1])

        if tone not in [1, 2, 3, 4]:
            return base + str(random.choice([1, 2, 3, 4]))

        possible_tones = [1, 2, 3, 4]
        possible_tones.remove(tone)
        new_tone = random.choice(possible_tones)
        return base + str(new_tone)

    def _calculate_replacement_probability(self, orig_freq, target_freq):
        """根据频率差计算替换概率（指数衰减）。"""
        if target_freq > orig_freq:
            return 1.0

        freq_diff = orig_freq - target_freq
        if freq_diff > self.max_freq_diff:
            return 0.0

        return math.exp(-3 * freq_diff / self.max_freq_diff)

    def _get_similar_frequency_chars(self, char, py, num_candidates=5):
        """获取与给定字频率相近的同音字，可能包含声调错误。"""
        homophones = []

        # 有一定概率使用错误声调
        if random.random() < self.tone_error_rate:
            wrong_tone_py = self._get_similar_tone_pinyin(py)
            homophones.extend(self.pinyin_dict[wrong_tone_py])

        # 添加正确声调的同音字
        homophones.extend(self.pinyin_dict[py])

        if not homophones:
            return None

        orig_freq = self.char_frequency.get(char, 0)

        # 计算所有同音字与原字的频率差，并过滤掉低频字
        freq_diff = [(h, self.char_frequency.get(h, 0))
                     for h in homophones
                     if h != char and self.char_frequency.get(h, 0) >= self.min_freq]

        if not freq_diff:
            return None

        # 计算每个候选字的替换概率
        candidates_with_prob = []
        for h, freq in freq_diff:
            prob = self._calculate_replacement_probability(orig_freq, freq)
            if prob > 0:
                candidates_with_prob.append((h, prob))

        if not candidates_with_prob:
            return None

        candidates_with_prob.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in candidates_with_prob[:num_candidates]]

    def _get_word_pinyin(self, word):
        """获取词语的拼音列表。"""
        return [py[0] for py in pinyin(word, style=Style.TONE3)]

    def _segment_sentence(self, sentence):
        """使用 jieba 分词，返回词语列表。"""
        return list(jieba.cut(sentence))

    def _get_jieba_word_freq(self) -> dict[str, float]:
        """ADAPTATION 2：jieba 词典词频表懒加载（原版每次调用重读）。"""
        if self._valid_words is None:
            valid_words: dict[str, float] = {}
            dict_path = os.path.join(os.path.dirname(jieba.__file__), 'dict.txt')
            try:
                with open(dict_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            try:
                                valid_words[parts[0]] = float(parts[1])
                            except ValueError:
                                continue
            except Exception as e:  # noqa: BLE001
                log.warning(jlog("typo_word_dict_failed", error=type(e).__name__))
            self._valid_words = valid_words
        return self._valid_words

    def _get_word_homophones(self, word):
        """获取整个词的同音词，只返回高频的有意义词语。"""
        if len(word) == 1:
            return []

        word_pinyin = self._get_word_pinyin(word)

        candidates = []
        for py in word_pinyin:
            chars = self.pinyin_dict.get(py, [])
            if not chars:
                return []
            candidates.append(chars)

        # 生成所有可能的组合
        all_combinations = itertools.product(*candidates)

        # ADAPTATION 2：词频表走缓存（原版此处每次重读 dict.txt）
        valid_words = self._get_jieba_word_freq()

        original_word_freq = valid_words.get(word, 0)
        min_word_freq = original_word_freq * 0.1

        homophones = []
        for combo in all_combinations:
            new_word = ''.join(combo)
            if new_word != word and new_word in valid_words:
                new_word_freq = valid_words[new_word]
                if new_word_freq >= min_word_freq:
                    char_avg_freq = sum(
                        self.char_frequency.get(c, 0) for c in new_word) / len(new_word)
                    combined_score = (new_word_freq * 0.7 + char_avg_freq * 0.3)
                    if combined_score >= self.min_freq:
                        homophones.append((new_word, combined_score))

        sorted_homophones = sorted(homophones, key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_homophones[:5]]

    def create_typo_sentence(self, sentence):
        """创建包含同音字错误的句子，支持词语级别和字级别的替换。

        返回: (typo_sentence, typo_info)
        """
        result = []
        typo_info = []

        words = self._segment_sentence(sentence)

        for word in words:
            # 标点符号或空格直接添加
            if all(not self._is_chinese_char(c) for c in word):
                result.append(word)
                continue

            word_pinyin = self._get_word_pinyin(word)

            # 尝试整词替换
            if len(word) > 1 and random.random() < self.word_replace_rate:
                word_homophones = self._get_word_homophones(word)
                if word_homophones:
                    typo_word = random.choice(word_homophones)
                    orig_freq = sum(self.char_frequency.get(c, 0) for c in word) / len(word)
                    typo_freq = sum(self.char_frequency.get(c, 0) for c in typo_word) / len(typo_word)
                    result.append(typo_word)
                    typo_info.append((word, typo_word,
                                      ' '.join(word_pinyin),
                                      ' '.join(self._get_word_pinyin(typo_word)),
                                      orig_freq, typo_freq))
                    continue

            # 单字替换
            if len(word) == 1:
                char = word
                py = word_pinyin[0]
                if random.random() < self.error_rate:
                    similar_chars = self._get_similar_frequency_chars(char, py)
                    if similar_chars:
                        typo_char = random.choice(similar_chars)
                        typo_freq = self.char_frequency.get(typo_char, 0)
                        orig_freq = self.char_frequency.get(char, 0)
                        replace_prob = self._calculate_replacement_probability(orig_freq, typo_freq)
                        if random.random() < replace_prob:
                            result.append(typo_char)
                            typo_py = pinyin(typo_char, style=Style.TONE3)[0][0]
                            typo_info.append((char, typo_char, py, typo_py, orig_freq, typo_freq))
                            continue
                result.append(char)
            else:
                # 多字词的单字替换（词中字替换概率随词长衰减）
                word_result = []
                for i, (char, py) in enumerate(zip(word, word_pinyin)):
                    word_error_rate = self.error_rate * (0.7 ** (len(word) - 1))

                    if random.random() < word_error_rate:
                        similar_chars = self._get_similar_frequency_chars(char, py)
                        if similar_chars:
                            typo_char = random.choice(similar_chars)
                            typo_freq = self.char_frequency.get(typo_char, 0)
                            orig_freq = self.char_frequency.get(char, 0)
                            replace_prob = self._calculate_replacement_probability(orig_freq, typo_freq)
                            if random.random() < replace_prob:
                                word_result.append(typo_char)
                                typo_py = pinyin(typo_char, style=Style.TONE3)[0][0]
                                typo_info.append((char, typo_char, py, typo_py, orig_freq, typo_freq))
                                continue
                    word_result.append(char)
                result.append(''.join(word_result))

        return ''.join(result), typo_info

    def set_params(self, **kwargs):
        """运行时调整参数（error_rate/min_freq/tone_error_rate/word_replace_rate）。"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                log.debug(jlog("typo_param_set", key=key, value=value))
            else:
                log.warning(jlog("typo_param_unknown", key=key))


#: 模块级单例（懒加载——构造要建拼音字典 + 字频表，首约 1-3s，别在 import 时做）
_typo_gen: ChineseTypoGenerator | None = None


def get_typo_generator() -> ChineseTypoGenerator:
    """懒加载错别字生成器单例（首次调用时构建，之后复用）。"""
    global _typo_gen
    if _typo_gen is None:
        _typo_gen = ChineseTypoGenerator(
            error_rate=0.03,      # 3% 单字同音替换（MaiMBot 线上值）
            min_freq=7,
            tone_error_rate=0.2,  # 20% 声调错误倾向
            word_replace_rate=0.02,  # 2% 整词替换
        )
    return _typo_gen
