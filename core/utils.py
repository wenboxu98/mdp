import torch
import random
import pprint
import numpy as np
from scipy.interpolate import interp1d
from terminaltables import AsciiTable
import os

def get_pred_activations(src, pred, config):
    src = minmax_norm(src)
    if len(src.size()) == 2:
        src = src.repeat((config.NUM_CLASSES, 1, 1)).permute(1, 2, 0)
    src_pred = src[0].cpu().numpy()[:, pred]
    src_pred = np.reshape(src_pred, (src.size(1), -1, 1))
    src_pred = upgrade_resolution(src_pred, config.UP_SCALE)
    return src_pred

def get_proposal_dict(cas_pred, aness_pred, pred, score_np, vid_num_seg, config):
    prop_dict = {}
    for th in config.CAS_THRESH:
        cas_tmp = cas_pred.copy()
        num_segments = cas_pred.shape[0]//config.UP_SCALE
        cas_tmp[cas_tmp[:, :, 0] < th] = 0
        seg_list = [np.where(cas_tmp[:, c, 0] > 0) for c in range(len(pred))]
        proposals = get_proposal_oic(seg_list, cas_tmp, score_np, pred, config.UP_SCALE, \
                        vid_num_seg, config.FEATS_FPS, num_segments)
        for i in range(len(proposals)):
            class_id = proposals[i][0][0]
            prop_dict[class_id] = prop_dict.get(class_id, []) + proposals[i]

    for th in config.ANESS_THRESH:
        aness_tmp = aness_pred.copy()
        num_segments = aness_pred.shape[0]//config.UP_SCALE
        aness_tmp[aness_tmp[:, :, 0] < th] = 0
        seg_list = [np.where(aness_tmp[:, c, 0] > 0) for c in range(len(pred))]
        proposals = get_proposal_oic(seg_list, cas_pred, score_np, pred, config.UP_SCALE, \
                        vid_num_seg, config.FEATS_FPS, num_segments)
        for i in range(len(proposals)):
            class_id = proposals[i][0][0]
            prop_dict[class_id] = prop_dict.get(class_id, []) + proposals[i]
    return prop_dict

def table_format(res_info, tIoU_thresh, title):
    table = [
        ['mAP@{:.1f}'.format(i) for i in tIoU_thresh],
        ['{:.4f}'.format(res_info['mAP@{:.1f}'.format(i)][-1]) for i in tIoU_thresh]     
    ]
    table[0].append('mAP@AVG')
    table[1].append('{:.4f}'.format(res_info["average_mAP"][-1]))
    
    col_num = len(table[0])
    table = AsciiTable(table, title)
    for i in range(col_num):
        table.justify_columns[i] = 'center'

    return '\n' + table.table + '\n'

def upgrade_resolution(arr, scale):
    x = np.arange(0, arr.shape[0])
    f = interp1d(x, arr, kind='linear', axis=0, fill_value='extrapolate')
    scale_x = np.arange(0, arr.shape[0], 1 / scale)
    up_scale = f(scale_x)
    return up_scale

def get_proposal_oic(tList, wtcam, final_score, c_pred, scale, v_len, sampling_frames, num_segments, _lambda=0.25, gamma=0.2):
    ## modified by xuwb 20241115 start
    # t_factor is utilized to scale the proposals to segment-level, 16 is a mistake
    
    # t_factor = (16 * v_len) / (scale * num_segments * sampling_frames)
    t_factor = (v_len) / (scale * num_segments)
    ## modified by xuwb 20241115 end
    temp = []
    for i in range(len(tList)):
        c_temp = []
        temp_list = np.array(tList[i])[0]
        if temp_list.any():
            grouped_temp_list = grouping(temp_list)
            for j in range(len(grouped_temp_list)):
                if len(grouped_temp_list[j]) < 2:
                    continue           
                inner_score = np.mean(wtcam[grouped_temp_list[j], i, 0])
                len_proposal = len(grouped_temp_list[j])
                outer_s = max(0, int(grouped_temp_list[j][0] - _lambda * len_proposal))
                outer_e = min(int(wtcam.shape[0] - 1), int(grouped_temp_list[j][-1] + _lambda * len_proposal))
                outer_temp_list = list(range(outer_s, int(grouped_temp_list[j][0]))) + list(range(int(grouped_temp_list[j][-1] + 1), outer_e + 1))               
                if len(outer_temp_list) == 0:
                    outer_score = 0
                else:
                    outer_score = np.mean(wtcam[outer_temp_list, i, 0])
                c_score = inner_score - outer_score + gamma * final_score[c_pred[i]]
                t_start = grouped_temp_list[j][0] * t_factor
                t_end = (grouped_temp_list[j][-1] + 1) * t_factor
                c_temp.append([c_pred[i], c_score, t_start, t_end])
            temp.append(c_temp)
    return temp

def result2json(result, class_dict):
    result_file = []
    class_idx2name = dict((v, k) for k, v in class_dict.items())
    for i in range(len(result)):
        for j in range(len(result[i])):
            line = {'label': class_idx2name[result[i][j][0]], 'score': result[i][j][1],
                    'segment': [result[i][j][2], result[i][j][3]]}
            result_file.append(line)
    return result_file

def grouping(arr):
    return np.split(arr, np.where(np.diff(arr) != 1)[0] + 1)

def save_best_record_thumos(test_info, file_path):
    fo = open(file_path, "w")
    fo.write("Step: {}\n".format(test_info["step"][-1]))
    fo.write("Test_acc: {:.4f}\n".format(test_info["test_acc"][-1]))
    # modified by xuwb 20241122 start 
    '''
    test_info["AP@0.5"].append(AP[0.5])
    test_info["AP@0.75"].append(AP[0.75])
    test_info["AP@0.95"].append(AP[0.95])
    test_info["AR@100"].append(AR[100])
    test_info["AR@50"].append(AR[50])
    test_info["AR@20"].append(AR[20])
    test_info["AR@10"].append(AR[10])
    fo.write("average_mAP: {:.4f}\n".format(test_info["average_mAP"][-1]))
    
    tIoU_thresh = np.linspace(0.1, 0.7, 7)
    for i in range(len(tIoU_thresh)):
        fo.write("mAP@{:.1f}: {:.4f}\n".format(tIoU_thresh[i], test_info["mAP@{:.1f}".format(tIoU_thresh[i])][-1]))
    fo.close()
    '''
    fo.write("AP@0.1: {:.4f}\n".format(test_info["AP@0.1"][-1]))
    fo.write("AP@0.2: {:.4f}\n".format(test_info["AP@0.2"][-1]))
    fo.write("AP@0.3: {:.4f}\n".format(test_info["AP@0.3"][-1]))
    fo.write("AP@0.4: {:.4f}\n".format(test_info["AP@0.4"][-1]))
    fo.write("AP@0.5: {:.4f}\n".format(test_info["AP@0.5"][-1]))
    fo.write("AP@0.6: {:.4f}\n".format(test_info["AP@0.6"][-1]))
    fo.write("AP@0.7: {:.4f}\n".format(test_info["AP@0.7"][-1]))
    fo.write("AP@0.75: {:.4f}\n".format(test_info["AP@0.75"][-1]))
    fo.write("AP@0.95: {:.4f}\n".format(test_info["AP@0.95"][-1]))
    fo.write("AR@20: {:.4f}\n".format(test_info["AR@20"][-1]))
    fo.write("AR@10: {:.4f}\n".format(test_info["AR@10"][-1]))
    fo.write("AR@5: {:.4f}\n".format(test_info["AR@5"][-1]))
    fo.write("AR@2: {:.4f}\n".format(test_info["AR@2"][-1]))
    fo.close()
    # modified by xuwb 20241122 end
  
def minmax_norm(act_map, min_val=None, max_val=None):
    if min_val is None or max_val is None:
        relu = torch.nn.ReLU()
        max_val = relu(torch.max(act_map, dim=1)[0])
        min_val = relu(torch.min(act_map, dim=1)[0])
    delta = max_val - min_val
    delta[delta <= 0] = 1
    ret = (act_map - min_val) / delta
    ret[ret > 1] = 1
    ret[ret < 0] = 0
    return ret

def nms(proposals, thresh):
    proposals = np.array(proposals)
    x1 = proposals[:, 2]
    x2 = proposals[:, 3]
    scores = proposals[:, 1]

    areas = x2 - x1 + 1
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(proposals[i].tolist())
        xx1 = np.maximum(x1[i], x1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])

        inter = np.maximum(0.0, xx2 - xx1 + 1)

        iou = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where(iou < thresh)[0]
        order = order[inds + 1]
    final_result = []
    for i in range(len(keep)):
        if keep[i][3] -keep[i][2] < 4:
            continue
        # if keep[i][1] < 0.5:
        #     continue
        final_result.append(keep[i])
        
    return final_result

def set_seed(seed):
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.cuda.manual_seed_all(seed)
    random.seed(seed)
    torch.backends.cudnn.deterministic=True
    torch.backends.cudnn.benchmark=False

def makedirs(dir):
    if not os.path.exists(dir): 
        os.makedirs(dir)

def set_path(config):
    if config.MODE == 'train':
        config.EXP_NAME = 'experiments/{cfg.MODE}/easy_{cfg.R_EASY}_hard_{cfg.R_HARD}' \
               '_m_{cfg.m}_M_{cfg.M}_freq_{cfg.TEST_FREQ}' \
               '_seed_{cfg.SEED}'.format(cfg=config)
        config.MODEL_PATH = os.path.join(config.EXP_NAME, 'model')
        config.LOG_PATH = os.path.join(config.EXP_NAME, 'log')
        makedirs(config.MODEL_PATH)
        makedirs(config.LOG_PATH)
    elif config.MODE == 'test':
        config.EXP_NAME = 'experiments/{cfg.MODE}'.format(cfg=config)
    
    config.OUTPUT_PATH = os.path.join(config.EXP_NAME, 'output')
    makedirs(config.OUTPUT_PATH)
    print('=> exprtiments folder: {}'.format(config.EXP_NAME))

def save_config(config):
    file_path = os.path.join(config.OUTPUT_PATH, "config.txt")
    fo = open(file_path, "w")
    fo.write("Configurtaions:\n")
    fo.write(pprint.pformat(config))
    fo.close()

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

def select_pairs(labels):
    selected_list = []
    labels = labels.unsqueeze(0).repeat(labels.shape[0],1,1)
    labels_T = labels.permute(1,0,2)
    select = (labels!=labels_T).sum(-1)
    for i in range(labels.shape[0]):
        for j in range(labels.shape[0]):
            if select[i,j]==0 and set([i,j]) not in selected_list and i!=j:
                selected_list.append(set([i,j]))
    return selected_list
