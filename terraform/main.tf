# =====================================================================================================
# 檔案名稱：main.tf
# 專案名稱：PCA DevOps Exam - Infrastructure as Code (IaC)
# 核心價值：透過宣告式代碼定義 GCP 資源，落實版本控制與自動化部署。
# =====================================================================================================

# ==========================================================
# 區塊零：Terraform 全域設定 (狀態管理與版本鎖定)
# ==========================================================
terraform {
  # [核心功能] 設定遠端狀態儲存 (Remote Backend)
  # 意義：透過 GCS 儲存 .tfstate，確保 GitHub Actions 執行時能抓到先前的部署紀錄。
  backend "gcs" {
    bucket  = "danny-terraform-state-bucket" # [自訂] GCP 儲存桶名稱，存放基礎設施的「記憶」
    prefix  = "terraform/state"             # [自訂] 狀態檔在儲存桶內的目錄路徑
  }

  # [SRE 關鍵] 鎖定供應商版本 (Provider Pinning)
  # 目的：避開 Google Provider v7.18.0 在處理大型整數時的數值溢位 (Value out of range) Bug。
  required_providers {
    google = {
      source  = "hashicorp/google"          # [固定] 指定官方 Google 供應商來源
      version = "~> 6.0"                    # [鎖定] 強制使用穩定的 6.x 系列，確保部署穩定性
    }
  }
}

# ----------------------------------------------------------
# 區塊一：設定雲端供應商 (Provider Configuration)
# ----------------------------------------------------------
provider "google" {                         # [固定] 宣告使用 Google Cloud 資源
  project = "danny-pca-lab"                 # [自訂] 你的 GCP 專案唯一 ID
  region  = "asia-east1"                    # [自訂] 預設部署區域 (台灣)
}

# ----------------------------------------------------------
# 區塊二：虛擬私有網路配置 (VPC & Subnet)
# ----------------------------------------------------------
# [資源] 建立 VPC 虛擬網路
resource "google_compute_network" "pca_vpc" {
  name                    = "pca-vpc"       # [自訂] 網站在雲端顯示的網路名稱
  auto_create_subnetworks = false           # [固定] 關閉自動建立子網，改用手動配置以符合安全稽核
}

# [資源] 建立 Subnet 子網路
resource "google_compute_subnetwork" "pca_subnet" {
  name          = "pca-subnet"              # [自訂] 子網路名稱
  ip_cidr_range = "10.0.1.0/24"             # [自訂] 定義私有 IP 網段範圍
  region        = "asia-east1"              # [固定] 必須與 Provider 區域一致
  
  # [引用] 透過 id 關聯上方建立的 VPC
  network       = google_compute_network.pca_vpc.id 
}

# ----------------------------------------------------------
# 區塊三：GKE 叢集主體設定 (Control Plane)
# ----------------------------------------------------------
resource "google_container_cluster" "pca-lab-gke" {
  name     = "pca-cluster"                  # [自訂] K8s 叢集名稱
  location = "asia-east1-a"                 # [自訂] 部署分區，建議選單一 Zone 以節省管理成本

  # [引用] 指定 GKE 運行在哪個 VPC 與 Subnet 內
  network    = google_compute_network.pca_vpc.name     
  subnetwork = google_compute_subnetwork.pca_subnet.name 

  # [優化] 移除預設節點池，因為我們要自訂更省錢的節點配置
  remove_default_node_pool = true      
  initial_node_count       = 1              # [參數] 初始啟動數量

  # [SRE 安全] 測試環境建議關閉刪除保護，方便 CI/CD 自動化清理資源
  deletion_protection = false
}

# ----------------------------------------------------------
# 區塊四：工作節點資源池 (Node Pool - Spot Instance)
# ----------------------------------------------------------
resource "google_container_node_pool" "pca-test-pool" {
  name       = "spot-pool"                  # [自訂] 節點池名稱
  location   = "asia-east1-a"               # [固定] 必須與叢集分區一致
  cluster    = google_container_cluster.pca-lab-gke.name # [引用] 關聯上述 GKE 叢集
  node_count = 1                            # [參數] 指定 1 台節點即可滿足 Demo 需求

  node_config {                             # [配置] 定義機器規格
    # [省錢大招] 啟用搶佔式實例 (Spot)，成本僅需原價 20~30%
    spot         = true                
    machine_type = "e2-medium"              # [自訂] 規格選用 2vCPU / 4GB RAM 
    disk_size_gb = 30                       # [參數] 30GB 硬碟空間 (測試環境足矣)

    # [授權] 賦予節點完整的 GCP 平台訪問權限，方便與日誌、監控系統整合
    oauth_scopes = [                   
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# ----------------------------------------------------------
# 區塊五：映像檔倉庫 (Artifact Registry)
# ----------------------------------------------------------
resource "google_artifact_registry_repository" "pca_repo" {
  location      = "asia-east1"              # [自訂] 倉庫存放區域
  repository_id = "pca-repo"                # [自訂] 倉庫唯一 ID，對應 Docker Push 路徑
  description   = "Docker repository for Flask App" # [描述] 
  format        = "DOCKER"                  # [固定] 指定格式為 Docker 映像檔
}

# ----------------------------------------------------------
# 區塊六：輸出結果 (Outputs)
# ----------------------------------------------------------
# [輸出] 完成後自動顯示叢集名稱，供 GitHub Actions 腳本連動
output "kubernetes_cluster_name" {
  value = google_container_cluster.pca-lab-gke.name 
}

# [輸出] 自動合成完整的 Artifact Registry URL
output "repository_url" {
  value = "${google_artifact_registry_repository.pca_repo.location}-docker.pkg.dev/${google_artifact_registry_repository.pca_repo.project}/${google_artifact_registry_repository.pca_repo.repository_id}" 
}