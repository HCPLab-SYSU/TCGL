"""Train 3D ConvNets to action classification."""
import os
import argparse
import time
import random

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split
from torchvision import transforms
import torch.optim as optim
from tensorboardX import SummaryWriter

from datasets.ucf101 import UCF101Dataset
from datasets.hmdb51 import HMDB51Dataset
from datasets.activitynet import ActivityDataset,ActivityDataset_linear
from models.c3d import C3D
from models.r3d import R3DNet
from models.r3d_50 import generate_model
from models.r21d import R2Plus1DNet
#from models.s3d_g import S3D


def train(args, model, criterion, optimizer, device, train_dataloader, writer, epoch):
    torch.set_grad_enabled(True)
    model.train()

    running_loss = 0.0
    correct = 0
    for i, data in enumerate(train_dataloader, 1):
        # get inputs
        clips, idxs = data
        inputs = clips.to(device)
        targets = idxs.to(device)
        # zero the parameter gradients
        optimizer.zero_grad()
        # forward and backward
        outputs = model(inputs) # return logits here
        loss = criterion(outputs, targets)
        loss.backward()
        optimizer.step()
        # compute loss and acc
        running_loss += loss.item()
        pts = torch.argmax(outputs, dim=1)
        correct += torch.sum(targets == pts).item()
        # print statistics and write summary every N batch
        if i % args.pf == 0:
            avg_loss = running_loss / args.pf
            avg_acc = correct / (args.pf * args.bs)
            print('[TRAIN] epoch-{}, batch-{}, loss: {:.3f}, acc: {:.3f}'.format(epoch, i, avg_loss, avg_acc))
            step = (epoch-1)*len(train_dataloader) + i
            writer.add_scalar('train/CrossEntropyLoss', avg_loss, step)
            writer.add_scalar('train/Accuracy', avg_acc, step)
            running_loss = 0.0
            correct = 0
    # summary params and grads per eopch
    #for name, param in model.named_parameters():
    #    writer.add_histogram('params/{}'.format(name), param, epoch)
    #    writer.add_histogram('grads/{}'.format(name), param.grad, epoch)


def validate(args, model, criterion, device, val_dataloader, writer, epoch):
    torch.set_grad_enabled(False)
    model.eval()
    
    total_loss = 0.0
    correct = 0
    for i, data in enumerate(val_dataloader):
        # get inputs
        clips, idxs = data
        inputs = clips.to(device)
        targets = idxs.to(device)
        # forward
        outputs = model(inputs) # return logits here
        loss = criterion(outputs, targets)
        # compute loss and acc
        total_loss += loss.item()
        pts = torch.argmax(outputs, dim=1)
        correct += torch.sum(targets == pts).item()
        # print('correct: {}, {}, {}'.format(correct, targets, pts))
    avg_loss = total_loss / len(val_dataloader)
    avg_acc = correct / len(val_dataloader.dataset)
    writer.add_scalar('val/CrossEntropyLoss', avg_loss, epoch)
    writer.add_scalar('val/Accuracy', avg_acc, epoch)
    print('[VAL] loss: {:.3f}, acc: {:.3f}'.format(avg_loss, avg_acc))
    return avg_loss


def test_backup(args, model, criterion, device, test_dataloader):
    torch.set_grad_enabled(False)
    model.eval()

    total_loss = 0.0
    correct = 0
    for i, data in enumerate(test_dataloader, 1):
        # get inputs
        clips, idxs = data
        inputs = clips.to(device)
        targets = idxs.to(device)
        # forward
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        # compute loss and acc
        total_loss += loss.item()
        pts = torch.argmax(outputs, dim=1)
        correct += torch.sum(targets == pts).item()
        # print('correct: {}, {}, {}'.format(correct, targets, pts))
    avg_loss = total_loss / len(test_dataloader)
    avg_acc = correct / len(test_dataloader.dataset)
    print('[TEST] loss: {:.3f}, acc: {:.3f}'.format(avg_loss, avg_acc))
    return avg_loss




def parse_args():
    parser = argparse.ArgumentParser(description='Video Classification')
    parser.add_argument('--mode', type=str, default='test', help='train/test')
    parser.add_argument('--model', type=str, default='r3d', help='c3d/r3d/r21d/r3d_50')
    parser.add_argument('--dataset', type=str, default='activity', help='ucf101/hmdb51/activity')
    parser.add_argument('--split', type=str, default='1', help='dataset split')
    parser.add_argument('--cl', type=int, default=16, help='clip length')
    parser.add_argument('--gpu', type=int, default=0, help='GPU id')
    parser.add_argument('--lr', type=float, default=1e-5, help='learning rate')
    parser.add_argument('--momentum', type=float, default=9e-1, help='momentum')
    parser.add_argument('--wd', type=float, default=5e-4, help='weight decay')
    parser.add_argument('--log', type=str, help='log directory')
    parser.add_argument('--ckpt', type=str, default='log/Major/UCF101_R3D50_TCG112_r3d_cl16_it8_tl3_12221043/best_acc_model_29.pt', help='checkpoint path')
    parser.add_argument('--desp', type=str, help='additional description')
    parser.add_argument('--epochs', type=int, default=20, help='number of total epochs to run')
    parser.add_argument('--start-epoch', type=int, default=1, help='manual epoch number (useful on restarts)')
    parser.add_argument('--bs', type=int, default=16, help='mini-batch size')
    parser.add_argument('--workers', type=int, default=4, help='number of data loading workers')
    parser.add_argument('--pf', type=int, default=100, help='print frequency every batch')
    parser.add_argument('--seed', type=int, default=632, help='seed for initializing training.')
    parser.add_argument('--output_dir', type=str, default='log/ActivityNet_Liner')
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = parse_args()
    print(vars(args))

    #torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = True
    # Force the pytorch to create context on the specific device 
    os.environ["CUDA_VISIBLE_DEVICES"] = "1"
    device = torch.device('cuda:0' if torch.cuda.is_available() else "cpu")

    if args.seed:
        random.seed(args.seed)
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)
        if args.gpu:
            torch.cuda.manual_seed_all(args.seed)

    ########### model ##############
    if args.dataset == 'ucf101':
        class_num = 101
    elif args.dataset == 'hmdb51':
        class_num = 51
    elif args.dataset == 'activity':
        class_num = 200

    if args.model == 'c3d':
        print(class_num)
        model = C3D(with_classifier=True, num_classes=class_num).cuda()
    elif args.model == 'r3d':
        model = R3DNet(layer_sizes=(3,4,6,3), with_classifier=False, num_classes=class_num).cuda()
    elif args.model == 'r21d':   
        model = R2Plus1DNet(layer_sizes=(1,1,1,1), with_classifier=True, num_classes=class_num).cuda()
    elif args.model == 's3d':
        model = S3D(num_classes=class_num, space_to_depth=False, with_classifier=True).cuda()
    elif args.model == 'r3d_50':
        model =generate_model(model_depth=50, with_classifier=False, return_conv=False).to(device)

    if torch.cuda.device_count() > 1:
        model = torch.nn.DataParallel(model, device_ids=[0]).cuda()
    if args.mode == 'train':  ########### Train #############
        if args.ckpt:  # resume training
            model.load_state_dict(torch.load(args.ckpt))
            log_dir = os.path.dirname(args.ckpt)
        else:
            if args.desp:
                exp_name = '{}_cl{}_{}_{}'.format(args.model, args.cl, args.desp, time.strftime('%m%d%H%M'))
            else:
                exp_name = '{}_cl{}_{}'.format(args.model, args.cl, time.strftime('%m%d%H%M'))
            log_dir = os.path.join(args.log, exp_name)
        writer = SummaryWriter(log_dir)

        train_transforms = transforms.Compose([
            transforms.Resize((128, 171)),
            transforms.RandomCrop(112),
            transforms.ToTensor()
        ])

        if args.dataset == 'ucf101':
            train_dataset = UCF101Dataset('data/ucf101', args.cl, args.split, True, train_transforms)
            val_size = 800
            train_dataset, val_dataset = random_split(train_dataset, (len(train_dataset)-val_size, val_size))
        elif args.dataset == 'hmdb51':
            train_dataset = HMDB51Dataset('data/hmdb51', args.cl, args.split, True, train_transforms)
            val_size = 400
            train_dataset, val_dataset = random_split(train_dataset, (len(train_dataset)-val_size, val_size))
        elif args.dataset == 'activity':
            train_dataset = ActivityDataset('D:/BaiduNetdiskDownload', args.cl, args.split, True, train_transforms)
            val_size = 1000

        print('TRAIN video number: {}, VAL video number: {}.'.format(len(train_dataset), len(val_dataset)))
        train_dataloader = DataLoader(train_dataset, batch_size=args.bs, shuffle=True,
                                    num_workers=args.workers, pin_memory=True)
        val_dataloader = DataLoader(val_dataset, batch_size=args.bs, shuffle=False,
                                    num_workers=args.workers, pin_memory=True)

        if args.ckpt:
            pass
        else:
            # save graph and clips_order samples
            for data in train_dataloader:
                clips, idxs = data
                writer.add_video('train/clips', clips, 0, fps=8)
                writer.add_text('train/idxs', str(idxs.tolist()), 0)
                clips = clips.to(device)
                writer.add_graph(model, clips)
                break
            # save init params at step 0
            for name, param in model.named_parameters():
                writer.add_histogram('params/{}'.format(name), param, 0)

        ### loss funciton, optimizer and scheduler ###
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.wd)
        #optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum, weight_decay=args.wd)
        #scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'min', min_lr=1e-5, patience=50, factor=0.1)
        scheduler = optim.lr_scheduler.MultiStepLR(optimizer, milestones=[150], gamma=0.1)

        prev_best_val_loss = float('inf')
        prev_best_model_path = None
        for epoch in range(args.start_epoch, args.start_epoch+args.epochs):
            time_start = time.time()
            train(args, model, criterion, optimizer, device, train_dataloader, writer, epoch)
            print('Epoch time: {:.2f} s.'.format(time.time() - time_start))
            val_loss = validate(args, model, criterion, device, val_dataloader, writer, epoch)
            # scheduler.step(val_loss)         
            writer.add_scalar('train/lr', optimizer.param_groups[0]['lr'], epoch)
            # save model every 20 epoches
            if epoch % 1 == 0:
                torch.save(model.state_dict(), os.path.join(log_dir, 'model_{}.pt'.format(epoch)))
            # save model for the best val
            if val_loss < prev_best_val_loss:
                model_path = os.path.join(log_dir, 'best_model_{}.pt'.format(epoch))
                torch.save(model.state_dict(), model_path)
                prev_best_val_loss = val_loss
                if prev_best_model_path:
                    os.remove(prev_best_model_path)
                prev_best_model_path = model_path
            scheduler.step()

    elif args.mode == 'test':  ########### Test #############
        pretrained_weights = torch.load(args.ckpt)['model']
        model.load_state_dict({k.replace('module.base_network.',''):v for k,v in pretrained_weights.items()},strict=False) 
        train_transforms = transforms.Compose([
            transforms.Resize((128, 171)),
            transforms.CenterCrop(112),
            transforms.ToTensor()
        ])
        test_transforms = transforms.Compose([
            transforms.Resize((128, 171)),
            transforms.CenterCrop(112),
            transforms.ToTensor()
        ])

        if args.dataset == 'ucf101':
            test_dataset = UCF101Dataset('data/ucf101', args.cl, args.split, False, test_transforms, 10)
        elif args.dataset == 'hmdb51':
            test_dataset = HMDB51Dataset('data/hmdb51', args.cl, args.split, False, test_transforms, 10)
        elif args.dataset == 'activity':
            train_dataset = ActivityDataset_linear('D:/BaiduNetdiskDownload', args.cl, args.split, True, train_transforms, 50)
            test_dataset = ActivityDataset_linear('D:/BaiduNetdiskDownload', args.cl, args.split, False, test_transforms, 50)

        train_dataloader = DataLoader(train_dataset, batch_size=1, shuffle=False,
                                num_workers=args.workers, pin_memory=True)
        test_dataloader = DataLoader(test_dataset, batch_size=1, shuffle=False,
                                num_workers=args.workers, pin_memory=True)
        print('TEST video number: {}.'.format(len(test_dataset)))
        torch.set_grad_enabled(False)
        model.eval()
        all_feat = []
        all_feat_cls = np.zeros([len(train_dataloader)], dtype=np.int32)
        with torch.no_grad():
            for idx, (data, cls) in enumerate(train_dataloader):
                print(idx)
                data_size = data.size()
                data = data.squeeze().cuda()
                feat = model(data).squeeze()
                feat_avg = torch.mean(feat, dim=0).view(-1)
                all_feat.append(feat_avg.data.cpu().numpy())
                all_feat_cls[idx] = cls.item()
        all_feat = np.stack(all_feat, axis=0)
        np.save(os.path.join(args.output_dir, 'feature_{}.npy'.format('train')), all_feat)
        np.save(os.path.join(args.output_dir, 'feature_{}_cls.npy'.format('train')), all_feat_cls)  
        all_feat = []
        all_feat_cls = np.zeros([len(test_dataloader)], dtype=np.int32)
        with torch.no_grad():
            for idx, (data, cls) in enumerate(test_dataloader):
                print(idx)
                data_size = data.size()
                data = data.squeeze().cuda()
                feat = model(data).squeeze()
                feat_avg = torch.mean(feat, dim=0).view(-1)
                all_feat.append(feat_avg.data.cpu().numpy())
                all_feat_cls[idx] = cls.item()
        all_feat = np.stack(all_feat, axis=0)
        np.save(os.path.join(args.output_dir, 'feature_{}.npy'.format('val')), all_feat)
        np.save(os.path.join(args.output_dir, 'feature_{}_cls.npy'.format('val')), all_feat_cls)  
