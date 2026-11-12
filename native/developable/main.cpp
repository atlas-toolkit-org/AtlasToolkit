// Atlas headless adaptation of Stein, Grinspun & Crane (2018).
// Optimizer adapted from their timestep.cpp. GPL-2.0-or-later; see GPLv2.txt.
#include <iostream>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <deque>
#include <chrono>
#include <Eigen/Dense>
#include <Eigen/Sparse>
#include <igl/doublearea.h>
#include <igl/readOBJ.h>
#include <igl/writeOBJ.h>
#include <igl/upsample.h>
#include <nlohmann/json.hpp>
#include "types.h"
#include "meshstruct.h"
#include <developableflow/energy_selector.h>
using json=nlohmann::json;
using Mat=Eigen::Matrix<double,Eigen::Dynamic,3>;
using Faces=Eigen::Matrix<int,Eigen::Dynamic,3>;
struct History { std::deque<Mat> s,y; void clear(){s.clear();y.clear();} };
double dot(const Mat&a,const Mat&b){return (a.array()*b.array()).sum();}
double min_double_area=1e-20; // Normalized-coordinate admissibility floor, scaled under refinement.
bool valid(const Mat&v,const Faces&f){
    if(!v.allFinite()||!f.rows()||!v.rows())return false;
    for(int i=0;i<f.rows();++i){
        if(f.row(i).minCoeff()<0||f.row(i).maxCoeff()>=v.rows())return false;
        Eigen::Vector3d a=v.row(f(i,1))-v.row(f(i,0)), b=v.row(f(i,2))-v.row(f(i,0));
        if(a.cross(b).squaredNorm()<min_double_area*min_double_area)return false;
    }return true;
}
void topology(const Mat&v,const Faces&f){
    if(!valid(v,f))throw std::runtime_error("invalid vertices, indices or degenerate triangles");
    std::map<std::pair<int,int>,std::pair<int,int>> edges;
    for(int i=0;i<f.rows();++i)for(int j=0;j<3;++j){
        int a=f(i,j),b=f(i,(j+1)%3); auto &e=edges[std::minmax(a,b)];
        ++e.first; e.second+=(a<b?1:-1);
    }
    for(auto&e:edges)if(e.second.first>2||(e.second.first==2&&e.second.second!=0))
        throw std::runtime_error("non-manifold edges or inconsistent winding");
    // Check vertex links, including bow-tie vertices that pass edge-manifold checks.
    std::vector<std::vector<int>> vf(v.rows());
    for(int i=0;i<f.rows();++i)for(int j=0;j<3;++j)vf[f(i,j)].push_back(i);
    for(int k=0;k<v.rows();++k){
        if(vf[k].empty())throw std::runtime_error("unreferenced vertex");
        std::map<int,std::vector<int>> link;
        for(int i:vf[k]){int a=-1,b=-1;for(int j=0;j<3;++j)if(f(i,j)!=k){if(a<0)a=f(i,j);else b=f(i,j);}link[a].push_back(b);link[b].push_back(a);}
        std::set<int> seen;std::vector<int> pending={link.begin()->first};int ends=0;
        for(auto&e:link){if(e.second.size()>2)throw std::runtime_error("non-manifold vertex link");if(e.second.size()==1)++ends;}
        while(!pending.empty()){int a=pending.back();pending.pop_back();if(!seen.insert(a).second)continue;for(int b:link[a])pending.push_back(b);}
        if(seen.size()!=link.size()||(ends!=0&&ends!=2))throw std::runtime_error("non-manifold vertex link");
    }
}
struct Evaluation {double value;Mat grad;Eigen::VectorXd energies;};
Evaluation evaluate(const Mat&v,const Faces&f,const Developables::Mesh&m,EnergyType kind){
    Evaluation e;
    energy_selector(kind,v,f,m.VF,m.VFi,m.isB,e.energies,e.grad);
    // Boundary terms are omitted by the paper. Also constrain their degrees of freedom.
    for(int i=0;i<v.rows();++i)if(m.isB[i])e.grad.row(i).setZero();
    e.value=e.energies.sum();
    if(!std::isfinite(e.value)||!e.grad.allFinite())throw std::runtime_error("non-finite energy or gradient");
    return e;
}
Mat direction(const Evaluation&e,History&h){
    Mat q=e.grad;
    std::vector<double> alpha(h.s.size()),rho(h.s.size());
    for(int i=int(h.s.size())-1;i>=0;--i){rho[i]=1./dot(h.y[i],h.s[i]);alpha[i]=rho[i]*dot(h.s[i],q);q-=alpha[i]*h.y[i];}
    if(!h.s.empty())q*=dot(h.s.back(),h.y.back())/dot(h.y.back(),h.y.back());
    for(size_t i=0;i<h.s.size();++i){double beta=rho[i]*dot(h.y[i],q);q+=h.s[i]*(alpha[i]-beta);}
    q=-q;
    if(!q.allFinite()||dot(q,e.grad)>=0){h.clear();q=-e.grad;}
    return q;
}
json optimize(Mat&v,Faces&f,const json&settings,EnergyType kind,int level,int levels){
    Developables::Mesh m(v,f); History h;auto e=evaluate(v,f,m,kind);
    double initial=e.value,step=1e-3;int steps=0;std::string stop="iteration limit";
    int maxsteps=settings.value("iterations",1000);double tolerance=settings.value("tolerance",1e-6);
    for(int it=0;it<maxsteps;++it){
        if(e.grad.norm()/std::sqrt(double(v.rows()))<=tolerance||e.value<=1e-14){stop="converged";break;}
        Mat p=direction(e,h),trial;double slope=dot(p,e.grad),lo=0,hi=std::numeric_limits<double>::infinity();
        double t=std::min(step*2.,100.);bool accepted=false;Evaluation candidate;
        // Lewis/Overton weak-Wolfe line search, with geometric admissibility checks.
        for(int ls=0;ls<80;++ls){
            trial=v+t*p;
            bool good=valid(trial,f);
            if(good){try{candidate=evaluate(trial,f,m,kind);}catch(const std::exception&){good=false;}}
            if(!good||candidate.value>e.value+1e-4*t*slope){hi=t;}
            else if(dot(p,candidate.grad)<0.99*slope){lo=t;}
            else{accepted=true;break;}
            t=std::isfinite(hi)?0.5*(lo+hi):std::min(t*2.,100.);
            if(t<1e-12||hi-lo<1e-14||t>=100.)break;
        }
        if(!accepted){
            // A nonsmooth seam can stall Wolfe. Retry monotone Armijo steepest descent.
            h.clear();p=-e.grad;slope=-dot(e.grad,e.grad);t=std::min(step,1e-3);
            for(int ls=0;ls<45;++ls){trial=v+t*p;
                if(valid(trial,f)){try{candidate=evaluate(trial,f,m,kind);accepted=candidate.value<=e.value+1e-4*t*slope;}catch(const std::exception&){} }
                if(accepted)break;t*=0.5;
            }
        }
        if(!accepted){stop="line search stalled";break;}
        Mat s=trial-v,y=candidate.grad-e.grad;
        if(dot(s,y)>1e-12*s.norm()*y.norm()){
            h.s.push_back(s);h.y.push_back(y);if(h.s.size()>8){h.s.pop_front();h.y.pop_front();}
        }else h.clear();
        v=trial;e=candidate;step=t;++steps;
        if(it%25==0)std::cout<<"PROGRESS "<<(level+double(it)/maxsteps)/levels<<" Level "<<level+1<<", step "<<it+1<<std::endl;
    }
    return {{"level",level},{"faces",f.rows()},{"initial_energy",initial},{"final_energy",e.value},
            {"gradient_rms",e.grad.norm()/std::sqrt(double(v.rows()))},{"steps",steps},{"status",stop}};
}
int main(int argc,char**argv){try{
    std::map<std::string,std::string> args;
    for(int i=1;i<argc;++i){std::string key=argv[i];if(key=="--version"){std::cout<<"atlas-developable-core 0.1.0 protocol 1\n";return 0;}
        if(i+1>=argc)throw std::runtime_error("expected --input, --settings, --output and --report");args[key]=argv[++i];}
    auto start=std::chrono::steady_clock::now();
    json settings;std::ifstream conf(args.at("--settings"));conf>>settings;
    int n=settings.value("subdivisions",2),it=settings.value("iterations",1000),cap=settings.value("max_faces",250000);
    double tol=settings.value("tolerance",1e-6);
    if(n<0||n>4||it<0||it>10000||cap<4||cap>2000000||!std::isfinite(tol)||tol<=0)throw std::runtime_error("invalid solver settings");
    std::string name=settings.value("energy",std::string("covariance"));EnergyType kind;
    if(name=="covariance")kind=ENERGY_TYPE_HINGE;else if(name=="max-covariance")kind=ENERGY_TYPE_MINWIDTH;
    else if(name=="combinatorial")kind=ENERGY_TYPE_PAIRWISENORMALS;else throw std::runtime_error("unknown energy");
    Eigen::MatrixXd input;Eigen::MatrixXi fi;if(!igl::readOBJ(args.at("--input"),input,fi)||input.cols()!=3||fi.cols()!=3)throw std::runtime_error("input must be a triangle OBJ");
    Mat v=input;Faces f=fi;topology(v,f);
    double smallest=std::numeric_limits<double>::infinity();
    for(int i=0;i<f.rows();++i){Eigen::Vector3d a=v.row(f(i,1))-v.row(f(i,0)),b=v.row(f(i,2))-v.row(f(i,0));smallest=std::min(smallest,a.cross(b).norm());}
    min_double_area=std::min(1e-6,smallest*0.05);
    long long total=f.rows();for(int l=0;l<n;++l)total*=4;if(total>cap)throw std::runtime_error("subdivision exceeds face budget");
    json report={{"schema",1},{"version","0.1.0"},{"solver_revision","67848c18c3b0c6389fef9b165a37fe7db5eed0f3"},{"levels",json::array()}};
    if(settings.value("evaluate_only",false)){
        Developables::Mesh m(v,f);auto e=evaluate(v,f,m,kind);
        report["energy"]=e.value;report["gradient"]=json::array();for(int i=0;i<v.rows();++i)report["gradient"].push_back({e.grad(i,0),e.grad(i,1),e.grad(i,2)});
    }else for(int level=0;level<=n;++level){
        report["levels"].push_back(optimize(v,f,settings,kind,level,n+1));
        if(level<n){Mat nextv;Faces nextf;igl::upsample(v,f,nextv,nextf);v=nextv;f=nextf;min_double_area*=0.24;topology(v,f);}
    }
    topology(v,f);if(!igl::writeOBJ(args.at("--output"),v,f))throw std::runtime_error("cannot write output OBJ");
    report["faces"]=f.rows();report["vertices"]=v.rows();report["seconds"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    std::ofstream out(args.at("--report"));out<<std::setw(2)<<report;if(!out)throw std::runtime_error("cannot write diagnostics");
    std::cout<<"PROGRESS 1 Complete"<<std::endl;return 0;
}catch(const std::exception&e){std::cerr<<"Developable solver: "<<e.what()<<std::endl;return 1;}}
