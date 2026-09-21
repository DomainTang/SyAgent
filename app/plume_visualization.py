# -*- coding: utf-8 -*-
import folium
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import math
import os
from datetime import datetime

from paths import DATA_DIR, OUTPUT_DIR

try:
    from scipy.spatial import ConvexHull
    from scipy.ndimage import gaussian_filter

    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("警告：scipy未安装，将使用简化的轮廓生成方法")


def parse_kml_complete(kml_path):
    """完整解析KML文件，提取所有点位和多边形信息"""
    points = {}
    polygons = {}

    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()

        # 处理所有Placemark元素（不使用命名空间）
        for placemark in root.findall('.//Placemark'):
            name_elem = placemark.find('.//name')
            name = name_elem.text.strip() if name_elem is not None and name_elem.text else "未命名"

            # 提取描述信息
            desc_elem = placemark.find('.//description')
            description = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""

            # 提取扩展数据
            extended_data = {}
            extended_data_elem = placemark.find('.//ExtendedData')
            if extended_data_elem is not None:
                for data in extended_data_elem.findall('.//Data'):
                    data_name = data.get('name', '')
                    value_elem = data.find('.//value')
                    if value_elem is not None and value_elem.text:
                        extended_data[data_name] = value_elem.text.strip()

            # 处理点位
            point_elem = placemark.find('.//Point')
            if point_elem is not None:
                coord_elem = point_elem.find('.//coordinates')
                if coord_elem is not None and coord_elem.text:
                    coords = coord_elem.text.strip().split(',')
                    if len(coords) >= 2:
                        try:
                            lon = float(coords[0])
                            lat = float(coords[1])
                            elev = float(coords[2]) if len(coords) > 2 else 0.0

                            points[name] = {
                                'lat': lat,
                                'lon': lon,
                                'elevation': elev,
                                'description': description,
                                'extended_data': extended_data
                            }
                        except ValueError as e:
                            print(f"解析点位坐标失败 {name}: {e}")

            # 处理多边形
            polygon_elem = placemark.find('.//Polygon')
            if polygon_elem is not None:
                outer_boundary = polygon_elem.find('.//outerBoundaryIs')
                if outer_boundary is not None:
                    linear_ring = outer_boundary.find('.//LinearRing')
                    if linear_ring is not None:
                        coord_elem = linear_ring.find('.//coordinates')
                        if coord_elem is not None and coord_elem.text:
                            coords_text = coord_elem.text.strip()
                            coord_pairs = coords_text.split()
                            poly_points = []

                            for pair in coord_pairs:
                                parts = pair.split(',')
                                if len(parts) >= 2:
                                    try:
                                        lon = float(parts[0])
                                        lat = float(parts[1])
                                        poly_points.append([lat, lon])
                                    except ValueError:
                                        continue

                            if poly_points:
                                polygons[name] = {
                                    'coordinates': poly_points,
                                    'description': description,
                                    'extended_data': extended_data
                                }

    except ET.ParseError as e:
        print(f"KML文件解析错误: {e}")
    except Exception as e:
        print(f"处理KML文件时发生错误: {e}")

    return points, polygons


def load_prediction_data(excel_path):
    """读取预测结果Excel文件"""
    try:
        df = pd.read_excel(excel_path, engine='openpyxl')
        print(f"Excel文件列名: {list(df.columns)}")
        print(f"数据形状: {df.shape}")
        print(f"前5行数据:\n{df.head()}")
        return df
    except Exception as e:
        print(f"读取Excel文件失败: {e}")
        return None


def load_voc_data_for_event(csv_root_dir, event_time, sensor_id):
    """根据事件时间和传感器ID加载对应的VOC数据"""
    try:
        year = event_time.year
        month = event_time.month
        day = event_time.day

        # 构建月份文件夹路径
        month_folder = f"{year}年{month}月"
        month_path = os.path.join(csv_root_dir, month_folder)

        # 构建CSV文件名
        csv_filename = f"jc_doseratedata_{year}_{month:02d}_{day:02d}.csv"
        csv_path = os.path.join(month_path, csv_filename)

        if not os.path.exists(csv_path):
            print(f"警告：未找到日期 {event_time.strftime('%Y-%m-%d')} 对应的CSV文件: {csv_path}")
            return None

        # 读取CSV文件
        df = pd.read_csv(csv_path, encoding='utf-8')

        # 处理列名
        if 'mn' in df.columns:
            df = df.rename(columns={'mn': 'sensor_id'})
        elif 'sno' in df.columns:
            df = df.rename(columns={'sno': 'sensor_id'})
        else:
            print("警告：CSV中未找到传感器ID列")
            return None

        if 'sj' not in df.columns:
            print("警告：CSV中未找到时间列")
            return None

        df = df.rename(columns={'sj': 'time'})
        df['sensor_id'] = df['sensor_id'].astype(str)
        df['sensor_id'] = df['sensor_id'].str[-3:]  # 取后三位
        df['time'] = pd.to_datetime(df['time'], format='%Y-%m-%d %H:%M:%S')
        df['voc'] = pd.to_numeric(df['voc'], errors='coerce')

        # 筛选指定传感器和时间窗口（±1分钟）
        target_sensor = str(sensor_id)[-3:]  # 确保格式一致
        time_window = pd.Timedelta(minutes=1)

        filtered_df = df[
            (df['sensor_id'] == target_sensor) &
            (df['time'] >= event_time - time_window) &
            (df['time'] <= event_time + time_window)
            ]

        if not filtered_df.empty:
            # 返回该时间窗口内的最大VOC值
            max_voc = filtered_df['voc'].max()
            print(f"传感器 {target_sensor} 在 {event_time} 附近的最大VOC值: {max_voc}")
            return max_voc
        else:
            print(f"警告：传感器 {target_sensor} 在 {event_time} 附近没有数据")
            return None

    except Exception as e:
        print(f"加载VOC数据时出错: {e}")
        return None


def wind_direction_to_angle(wind_dir):
    """将风向转换为角度（度）"""
    wind_directions = {
        'N': 90, '北': 90, '北风': 90,
        'NNE': 67.5, '北北东': 67.5,
        'NE': 45, '东北': 45, '东北风': 45,
        'ENE': 22.5, '东北东': 22.5,
        'E': 0, '东': 0, '东风': 0,
        'ESE': 337.5, '东南东': 337.5,
        'SE': 315, '东南': 315, '东南风': 315,
        'SSE': 292.5, '南南东': 292.5,
        'S': 270, '南': 270, '南风': 270,
        'SSW': 247.5, '南南西': 247.5,
        'SW': 225, '西南': 225, '西南风': 225,
        'WSW': 202.5, '西南西': 202.5,
        'W': 180, '西': 180, '西风': 180,
        'WNW': 157.5, '西北西': 157.5,
        'NW': 135, '西北': 135, '西北风': 135,
        'NNW': 112.5, '北北西': 112.5
    }

    if isinstance(wind_dir, str):
        return wind_directions.get(wind_dir.strip(), 0)
    elif isinstance(wind_dir, (int, float)):
        return float(wind_dir)
    else:
        return 0


def wind_speed_level_to_ms(wind_level):
    """将风速等级转换为米/秒"""
    # 蒲福风级对应的风速（米/秒）
    wind_scale = {
        0: 0.2, 1: 1.0, 2: 2.5, 3: 4.5, 4: 6.5,
        5: 9.0, 6: 12.0, 7: 15.5, 8: 19.5, 9: 23.5,
        10: 27.5, 11: 31.5, 12: 35.0
    }

    if isinstance(wind_level, str):
        try:
            level = int(wind_level.replace('级', '').replace('风', ''))
            return wind_scale.get(level, 5.0)
        except:
            return 5.0
    elif isinstance(wind_level, (int, float)):
        return wind_scale.get(int(wind_level), 5.0)
    else:
        return 5.0


def latlon_to_xy(lat, lon, lat0, lon0):
    """将经纬度转换为以(lat0, lon0)为原点的平面坐标(x, y)，单位：米"""
    lat0_rad = math.radians(lat0)
    dy = (lat - lat0) * 110540.0
    dx = (lon - lon0) * 111320.0 * math.cos(lat0_rad)
    return dx, dy


def xy_to_latlon(x, y, lat0, lon0):
    """将平面坐标(x,y)米转换为经纬度，原点(lat0, lon0)"""
    lat = lat0 + (y / 110540.0)
    lon = lon0 + (x / (111320.0 * math.cos(math.radians(lat0))))
    return lat, lon


def enhanced_chemical_plant_gaussian_model(x, y, Q, u, H=1.0, stability='D', building_height=10.0,
                                           plant_complexity=1.2):
    """增强的化工厂高斯烟羽浓度分布模型

    Args:
        x, y: 坐标数组
        Q: 源强度
        u: 风速 (m/s)
        H: 源高度 (m)
        stability: 大气稳定度等级
        building_height: 周围建筑物平均高度 (m)
        plant_complexity: 化工厂复杂度系数 (1.0-2.0)
    """
    x_arr = np.array(x, dtype=float)
    y_arr = np.array(y, dtype=float)
    C = np.zeros_like(x_arr, dtype=float)

    # 只处理下风向区域
    mask = x_arr > 0
    if not np.any(mask):
        return C

    xx = x_arr[mask]
    yy = y_arr[mask]

    # 化工厂专用扩散参数（考虑建筑物和复杂地形影响）
    stability_params = {
        'A': (0.40, 0.30, 0.55, 0.00, 1.15),  # 极不稳定 (增强横向扩散)
        'B': (0.28, 0.20, 0.52, 0.00, 1.10),
        'C': (0.20, 0.15, 0.48, 0.25, 1.05),  # 弱不稳定
        'D': (0.15, 0.10, 0.48, 0.45, 1.00),  # 中性(默认)
        'E': (0.10, 0.08, 0.48, 0.70, 0.95),  # 弱稳定
        'F': (0.08, 0.06, 0.48, 0.90, 0.90)  # 极稳定
    }

    a, c, d, f, turb_factor = stability_params.get(stability, (0.15, 0.10, 0.48, 0.45, 1.00))
    b = 0.92  # 化工厂环境下的指数参数

    # 计算下风向距离 (考虑最小距离避免除零)
    downwind = np.maximum(3.0, xx)  # 减小最小距离以提高近场精度

    # 建筑物影响修正
    building_effect = 1.0 + (building_height / (building_height + downwind)) * 0.3

    # 计算扩散参数 (Briggs公式 + 化工厂修正)
    sigma_y = a * (downwind ** b) * building_effect * plant_complexity * turb_factor
    sigma_z = (c * (downwind ** d) + f) * building_effect

    # 添加化工厂特有的近场湍流增强
    near_field_enhancement = np.exp(-downwind / 50.0) * 0.5 + 1.0
    sigma_y *= near_field_enhancement
    sigma_z *= near_field_enhancement

    # 风速修正（考虑建筑物阻挡效应）
    effective_wind_speed = u * (1.0 - np.exp(-downwind / 100.0) * 0.3)

    # 增强的高斯烟羽方程
    term1 = Q / (2 * np.pi * effective_wind_speed * sigma_y * sigma_z)
    term2 = np.exp(-yy ** 2 / (2 * sigma_y ** 2))

    # 垂直扩散项（考虑地面反射和建筑物影响）
    term3_direct = np.exp(-(H ** 2) / (2 * sigma_z ** 2))
    term3_reflect = np.exp(-((2 * building_height - H) ** 2) / (2 * sigma_z ** 2))
    term3 = term3_direct + term3_reflect

    # 化工厂环境的复合衰减因子
    # 1. 大气扩散衰减
    atmospheric_decay = np.exp(-downwind / 800.0)
    # 2. 化学反应衰减（适用于活性气体）
    chemical_decay = np.exp(-downwind / 1200.0)
    # 3. 沉降衰减
    deposition_decay = np.exp(-downwind / 1500.0)

    total_decay = atmospheric_decay * chemical_decay * deposition_decay

    C_sub = term1 * term2 * term3 * total_decay
    C[mask] = C_sub
    return C


def create_gradient_heatmap_overlay(concentration_grid, x_vals, y_vals, source_lat, source_lon, max_voc_value):
    """创建梯度热力图覆盖层，替代边界线显示"""
    try:
        # 应用高斯平滑以创建更自然的梯度
        if SCIPY_AVAILABLE:
            smoothed_grid = gaussian_filter(concentration_grid, sigma=3.0)
        else:
            smoothed_grid = concentration_grid

        # 标准化浓度值到0-1范围
        max_conc = np.max(smoothed_grid)
        if max_conc <= 0:
            return None

        normalized_grid = smoothed_grid / max_conc

        # 设置最小显示阈值（避免显示过低浓度）
        min_threshold = 0.01
        normalized_grid[normalized_grid < min_threshold] = 0

        # 创建颜色映射数据
        heat_data = []
        for i in range(0, len(y_vals), 3):  # 降低分辨率以提高性能
            for j in range(0, len(x_vals), 3):
                if normalized_grid[i, j] > 0:
                    # 转换为经纬度
                    lat_p, lon_p = xy_to_latlon(x_vals[j], y_vals[i], source_lat, source_lon)
                    # 强度值（0-1）
                    intensity = float(normalized_grid[i, j])
                    heat_data.append([lat_p, lon_p, intensity])

        return heat_data

    except Exception as e:
        print(f"创建梯度热力图时出错: {e}")
        return None


def create_enhanced_plume_contour(concentration_grid, x_vals, y_vals, source_lat, source_lon, level,
                                  smoothing_sigma=2.0):
    """创建增强的烟羽扩散轮廓"""
    try:
        # 应用高斯平滑
        if SCIPY_AVAILABLE:
            smoothed_grid = gaussian_filter(concentration_grid, sigma=smoothing_sigma)
        else:
            smoothed_grid = concentration_grid

        # 使用更精细的等高线方法
        import matplotlib.pyplot as plt
        from matplotlib.path import Path

        # 创建临时图形来生成等高线
        fig, ax = plt.subplots(figsize=(1, 1))
        ax.set_aspect('equal')

        # 生成等高线
        cs = ax.contour(x_vals, y_vals, smoothed_grid, levels=[level])

        contour_paths = []
        for collection in cs.collections:
            for path in collection.get_paths():
                vertices = path.vertices
                if len(vertices) > 3:
                    # 转换为经纬度坐标
                    lat_lon_vertices = []
                    for x, y in vertices:
                        lat_p, lon_p = xy_to_latlon(x, y, source_lat, source_lon)
                        lat_lon_vertices.append([lat_p, lon_p])

                    if len(lat_lon_vertices) > 3:
                        contour_paths.append(lat_lon_vertices)

        plt.close(fig)

        # 返回最大的轮廓
        if contour_paths:
            largest_contour = max(contour_paths, key=len)
            return largest_contour
        else:
            return None

    except Exception as e:
        print(f"增强轮廓生成失败，使用备用方法: {e}")
        # 备用方法：基于阈值的点集合
        valid_points = []
        for i in range(len(y_vals)):
            for j in range(len(x_vals)):
                if concentration_grid[i, j] >= level:
                    lat_p, lon_p = xy_to_latlon(x_vals[j], y_vals[i], source_lat, source_lon)
                    valid_points.append([lat_p, lon_p])

        if len(valid_points) < 4:
            return None

        if SCIPY_AVAILABLE:
            try:
                points_array = np.array(valid_points)
                hull = ConvexHull(points_array)
                hull_points = points_array[hull.vertices]
                return hull_points.tolist()
            except:
                return valid_points[:20] if len(valid_points) > 20 else valid_points
        else:
            return valid_points[:20] if len(valid_points) > 20 else valid_points


def create_plume_visualization(kml_path, excel_path, output_html_path=None, current_column_index=0, csv_root_dir=None):
    """创建烟羽扩散可视化地图"""

    # 解析KML文件
    print(f"正在解析KML文件: {kml_path}")
    points, polygons = parse_kml_complete(kml_path)
    print(f"解析完成：{len(points)}个点位，{len(polygons)}个区域")

    # 读取预测结果数据
    print(f"正在读取Excel文件: {excel_path}")
    df = load_prediction_data(excel_path)
    if df is None:
        return None

    # 检查列是否存在，建立列映射
    required_columns = ['经度', '纬度', '风速(级)', '风向', '疑似源分析', '时间', '传感器编号']
    column_mapping = {}

    for col in required_columns:
        if col in df.columns:
            column_mapping[col] = col
        else:
            # 尝试找到相似的列名
            for df_col in df.columns:
                if '经度' in col and ('经度' in df_col or 'lon' in df_col.lower() or 'lng' in df_col.lower()):
                    column_mapping[col] = df_col
                    break
                elif '纬度' in col and ('纬度' in df_col or 'lat' in df_col.lower()):
                    column_mapping[col] = df_col
                    break
                elif '风速' in col and ('风速' in df_col or 'wind_speed' in df_col.lower()):
                    column_mapping[col] = df_col
                    break
                elif '风向' in col and ('风向' in df_col or 'wind_dir' in df_col.lower()):
                    column_mapping[col] = df_col
                    break
                elif '疑似源分析' in col and (
                        '疑似源分析' in df_col or '原始记录' in df_col or '记录' in df_col or 'record' in df_col.lower() or 'analysis' in df_col.lower()):
                    column_mapping[col] = df_col
                    break
                elif '时间' in col and ('时间' in df_col or 'time' in df_col.lower() or '日期' in df_col):
                    column_mapping[col] = df_col
                    break
                elif '传感器编号' in col and ('传感器编号' in df_col or 'sensor' in df_col.lower() or '编号' in df_col):
                    column_mapping[col] = df_col
                    break

    # 初始化地图中心点和缩放级别
    # 默认使用所有点的中心
    if points:
        lats = [point['lat'] for point in points.values()]
        lons = [point['lon'] for point in points.values()]
        default_center_lat = sum(lats) / len(lats)
        default_center_lon = sum(lons) / len(lons)
    else:
        # 默认中心点（安庆地区）
        default_center_lat = 30.5255
        default_center_lon = 117.0580

    # 检查是否有当前事件的数据
    event_lat = default_center_lat
    event_lon = default_center_lon
    zoom_level = 16  # 基础缩放级别

    if current_column_index < len(df) and '经度' in column_mapping and '纬度' in column_mapping:
        row = df.iloc[current_column_index]
        try:
            event_lon = float(row[column_mapping['经度']])
            event_lat = float(row[column_mapping['纬度']])
            print(f"事件点坐标: ({event_lat:.6f}, {event_lon:.6f})")

            # 将地图中心设置为事件点，并增加150%的缩放
            # Folium的缩放级别是对数的，每增加1级，地图放大2倍
            # 150%缩放相当于增加约0.58个缩放级别 (log2(1.5) ≈ 0.58)
            zoom_level = 16  # 增加缩放级别，从14调整到15（相当于放大约200%）
            # 如果需要更精确的150%，可以使用14.5，但folium只接受整数
            # 所以我们使用15，这提供了良好的细节视图

        except Exception as e:
            print(f"解析事件坐标失败: {e}")
            event_lat = default_center_lat
            event_lon = default_center_lon

    # 创建Folium地图，以事件点为中心，缩放150%
    m = folium.Map(
        location=[event_lat, event_lon],  # 以事件点为中心
        zoom_start=zoom_level,  # 增强的缩放级别
        tiles=None
    )

    # 添加多个底图图层
    folium.TileLayer(
        tiles='OpenStreetMap',
        name='OpenStreetMap',
        attr='© OpenStreetMap contributors'
    ).add_to(m)

    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        name='卫星图像',
        attr='© Esri, Maxar, GeoEye, Earthstar Geographics, CNES/Airbus DS, USDA, USGS, AeroGRID, IGN, and the GIS User Community'
    ).add_to(m)

    # 添加区域多边形
    colors = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'lightred',
              'beige', 'darkblue', 'darkgreen', 'cadetblue', 'darkpurple', 'white',
              'pink', 'lightblue', 'lightgreen', 'gray', 'black', 'lightgray']

    for i, (name, polygon_data) in enumerate(polygons.items()):
        color = colors[i % len(colors)]

        popup_html = f"<b>区域名称:</b> {name}<br>"
        if polygon_data['description']:
            popup_html += f"<b>描述:</b> {polygon_data['description']}<br>"

        if polygon_data['extended_data']:
            popup_html += "<b>详细信息:</b><br>"
            for key, value in polygon_data['extended_data'].items():
                popup_html += f"&nbsp;&nbsp;{key}: {value}<br>"

        popup_html += f"<b>坐标点数:</b> {len(polygon_data['coordinates'])}"

        folium.Polygon(
            locations=polygon_data['coordinates'],
            color=color,
            weight=2,
            fillColor=color,
            fillOpacity=0.2,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"区域: {name}"
        ).add_to(m)

    # 添加监测点位
    for name, point_data in points.items():
        popup_html = f"<b>点位名称:</b> {name}<br>"
        popup_html += f"<b>经度:</b> {point_data['lon']:.6f}<br>"
        popup_html += f"<b>纬度:</b> {point_data['lat']:.6f}<br>"
        popup_html += f"<b>海拔:</b> {point_data['elevation']:.2f}m<br>"

        if point_data['description']:
            popup_html += f"<b>描述:</b> {point_data['description']}<br>"

        if point_data['extended_data']:
            popup_html += "<b>详细信息:</b><br>"
            for key, value in point_data['extended_data'].items():
                popup_html += f"&nbsp;&nbsp;{key}: {value}<br>"

        if '传感器' in name or 'sensor' in name.lower():
            icon_color = 'red'
            icon = 'info-sign'
        elif '监测' in name:
            icon_color = 'blue'
            icon = 'eye-open'
        else:
            icon_color = 'green'
            icon = 'map-marker'

        folium.Marker(
            location=[point_data['lat'], point_data['lon']],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"点位: {name}",
            icon=folium.Icon(color=icon_color, icon=icon)
        ).add_to(m)

    # 处理预测结果数据并添加烟羽扩散
    try:
        print(f"列映射: {column_mapping}")

        # 只处理当前指定的列（事件）
        if current_column_index < len(df):
            row = df.iloc[current_column_index]
            try:
                # 获取泄漏源位置（确保使用表格中的经纬度数据）
                if '经度' in column_mapping and '纬度' in column_mapping:
                    source_lon = float(row[column_mapping['经度']])
                    source_lat = float(row[column_mapping['纬度']])
                    print(f"烟羽起始点坐标: ({source_lat:.6f}, {source_lon:.6f})")
                else:
                    print(f"警告：第{current_column_index + 1}行缺少经纬度信息")
                    return m

                # 获取风速和风向
                if '风速(级)' in column_mapping:
                    wind_speed = wind_speed_level_to_ms(row[column_mapping['风速(级)']])
                else:
                    wind_speed = 5.0  # 默认风速

                if '风向' in column_mapping:
                    wind_dir = wind_direction_to_angle(row[column_mapping['风向']])
                else:
                    wind_dir = 0  # 默认风向

                # 获取疑似源分析
                if '疑似源分析' in column_mapping:
                    source_analysis = str(row[column_mapping['疑似源分析']])
                else:
                    source_analysis = f"事件{current_column_index + 1}"

                # 获取事件时间和传感器编号（用于加载VOC数据）
                event_time = None
                sensor_id = None
                max_voc_value = 100.0  # 默认值

                if '时间' in column_mapping:
                    try:
                        event_time = pd.to_datetime(row[column_mapping['时间']])
                    except:
                        print("警告：无法解析事件时间")

                if '传感器编号' in column_mapping:
                    sensor_id = str(row[column_mapping['传感器编号']])

                # 如果有CSV数据目录，尝试加载实际VOC数据
                if csv_root_dir and event_time and sensor_id:
                    voc_value = load_voc_data_for_event(csv_root_dir, event_time, sensor_id)
                    if voc_value is not None:
                        max_voc_value = voc_value
                        print(f"使用实际VOC数据作为最高浓度值: {max_voc_value}")
                    else:
                        print(f"未找到VOC数据，使用默认值: {max_voc_value}")

                # 添加泄漏源标记（确保位置正确）
                folium.Marker(
                    location=[source_lat, source_lon],  # 确保使用表格中的坐标
                    popup=folium.Popup(
                        f"<b>疑似源分析:</b> {source_analysis}<br><b>风速:</b> {wind_speed:.1f}m/s<br><b>风向:</b> {wind_dir}°<br><b>最大VOC值:</b> {max_voc_value:.1f}",
                        max_width=300),
                    tooltip=f"疑似源: {source_analysis}",
                    icon=folium.Icon(color='red', icon='warning-sign', prefix='glyphicon')
                ).add_to(m)

                # 创建高斯烟羽扩散可视化 - 300米范围，增强可视化
                max_distance_meters = 300  # 300米范围
                grid_resolution = 120  # 增加网格分辨率

                # 生成网格点（以泄漏源为中心，确保起点正确）
                x_vals = np.linspace(-max_distance_meters, max_distance_meters, grid_resolution)
                y_vals = np.linspace(-max_distance_meters, max_distance_meters, grid_resolution)
                xx, yy = np.meshgrid(x_vals, y_vals)
                print('wind_dir:',wind_dir)
                # 计算烟羽扩散方向（风向的相反方向）
                plume_dir = (wind_dir + 180.0) % 360.0

                theta_rad = math.radians(plume_dir)
                cos_t = math.cos(theta_rad)
                sin_t = math.sin(theta_rad)

                # 旋转坐标系：x轴指向下风向，y轴为横风向
                x_rot = xx * cos_t + yy * sin_t  # 下风向距离
                y_rot = -xx * sin_t + yy * cos_t  # 横风向距离

                # 使用增强的化工厂高斯烟羽浓度计算方法
                C_grid = enhanced_chemical_plant_gaussian_model(
                    x_rot, y_rot,
                    Q=max_voc_value * 15,  # 增强源强度以适应化工厂环境
                    u=wind_speed,
                    H=2.0,  # 化工厂典型排放高度
                    stability='D',
                    building_height=12.0,  # 化工厂建筑物平均高度
                    plant_complexity=1.3  # 化工厂复杂度系数
                )

                # 应用掩码和平滑处理
                max_conc = np.max(C_grid)
                if max_conc > 0:
                    print(f"烟羽浓度范围: {np.min(C_grid):.6f} - {max_conc:.6f}")

                    # 创建梯度热力图覆盖层（替代边界线）
                    heat_data = create_gradient_heatmap_overlay(
                        C_grid, x_vals, y_vals, source_lat, source_lon, max_voc_value
                    )

                    if heat_data and len(heat_data) > 0:
                        # 添加热力图插件
                        from folium.plugins import HeatMap

                        # 创建热力图层
                        HeatMap(
                            heat_data,
                            min_opacity=0.1,
                            max_zoom=18,
                            radius=25,  # 热点半径
                            blur=20,  # 模糊程度
                            gradient={
                                0.0: 'transparent',
                                0.2: 'blue',
                                0.4: 'cyan',
                                0.6: 'lime',
                                0.8: 'yellow',
                                1.0: 'red'
                            }
                        ).add_to(m)

                        print(f"成功创建包含 {len(heat_data)} 个数据点的梯度热力图")
                    else:
                        print("热力图数据生成失败")

                    # 可选：添加浓度等值线（虚线，用于参考）
                    reference_levels = [max_conc * 0.5, max_conc * 0.1]
                    for level in reference_levels:
                        contour = create_enhanced_plume_contour(
                            C_grid, x_vals, y_vals, source_lat, source_lon, level, smoothing_sigma=2.5
                        )
                        if contour and len(contour) > 3:
                            folium.Polygon(
                                locations=contour,
                                color='white',
                                weight=1,
                                fillColor='none',
                                fillOpacity=0,
                                dashArray='5, 5',  # 虚线样式
                                popup=f"参考浓度线: {level:.3f}",
                                tooltip=f"浓度参考线 (≥{level:.3f})"
                            ).add_to(m)

            except Exception as e:
                print(f"处理第{current_column_index + 1}行数据时出错: {e}")

    except Exception as e:
        print(f"处理预测数据时出错: {e}")

    # 添加当前事件的疑似源分析信息到左上角
    current_analysis = ""
    if df is not None and current_column_index < len(df):
        row = df.iloc[current_column_index]
        if '疑似源分析' in column_mapping:
            current_analysis = str(row[column_mapping['疑似源分析']])
        else:
            current_analysis = f"事件{current_column_index + 1}的疑似源分析"
    else:
        current_analysis = "无疑似源分析信息"

    title_html = f'''
    <div style="position: fixed; 
                top: 10px; left: 50px; width: 400px; height: auto; max-height: 300px;
                background-color: white; border: 2px solid grey; border-radius: 5px;
                z-index:9999; font-size:12px;
                padding: 10px; overflow-y: auto;
                ">
    <p style="margin: 0; font-weight: bold; font-size: 14px;">安庆监测点位及烟羽扩散分析</p>
    <hr style="margin: 5px 0;">
    <p style="margin: 0; font-weight: bold;">当前事件疑似源分析:</p>
    <div style="font-size: 11px; margin-top: 5px; background-color: #f0f0f0; padding: 8px; border-radius: 3px;">
    {current_analysis}
    </div>
    <p style="margin: 5px 0 0 0; font-size: 10px; color: #666;">事件编号: {current_column_index + 1}</p>
    <p style="margin: 5px 0 0 0; font-size: 10px; color: #333;">地图已放大150%并居中于事件点</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))

    # 添加图层控制
    folium.LayerControl().add_to(m)

    # 保存地图
    if output_html_path is None:
        output_html_path = "plume_visualization.html"

    m.save(output_html_path)
    print(f"烟羽扩散可视化地图已保存到: {output_html_path}")

    return m


def main():
    """主函数"""
    kml_file_path = os.path.join(DATA_DIR, "安庆监测点位及分区 202403.kml")
    excel_file_path = os.path.join(OUTPUT_DIR, "预测结果.xlsx")
    output_html_path = os.path.join(OUTPUT_DIR, "烟羽扩散可视化.html")

    # CSV数据根目录（可选，如果提供则会加载实际VOC数据）
    csv_root_dir = r"F:\2023中石化\安庆监测历史数据\东门大气站"

    # 检查文件是否存在
    if not os.path.exists(kml_file_path):
        print(f"错误：KML文件不存在 - {kml_file_path}")
        return

    if not os.path.exists(excel_file_path):
        print(f"错误：Excel文件不存在 - {excel_file_path}")
        return

    try:
        # 创建烟羽扩散可视化（默认显示第一个事件，索引为0）
        current_event_index = 0  # 可以修改这个值来显示不同的事件
        map_obj = create_plume_visualization(
            kml_file_path,
            excel_file_path,
            output_html_path,
            current_event_index,
            csv_root_dir  # 传入CSV数据目录
        )
        if map_obj:
            print("\n=== 烟羽扩散可视化完成 ===")
            print(f"输出文件: {output_html_path}")
            print(f"当前显示事件: {current_event_index + 1}")
            print("请在浏览器中打开HTML文件查看交互式地图")
            print("\n功能说明:")
            print("- 红色警告标记：疑似泄漏源位置（基于表格中的经纬度数据）")
            print("- 彩色多边形：高斯烟羽扩散浓度分布（300米范围，平滑边界）")
            print("- 扩散方向：风向的相反方向")
            print("- 地图已朝事件点缩放150%")
            print("- 左上角：显示当前事件的疑似源分析信息")
            print("- 右上角：图层控制")
            print("\n提示：修改main()函数中的current_event_index值可显示不同事件")

    except Exception as e:
        print(f"创建可视化时发生错误: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()