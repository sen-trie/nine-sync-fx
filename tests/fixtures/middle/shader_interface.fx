// Independent D3D9 interface-array, declaration-padding and duplicate-pass readout.
struct O {float4 p:POSITION;float2 uv[2]:TEXCOORD2;};
O VS(float4 p:POSITION,float4 uv:TEXCOORD0) {
 O o;o.p=p;o.uv[0]=uv.xy;o.uv[1]=uv.zw;return o;
}
float4 First(O i):COLOR {return float4(i.uv[0],i.uv[1].y,1);}
float4 Last(O i):COLOR {return float4(1,0,0,1);}
technique Main <string MMDPass="object";string Script="Pass=Same;";> {
 pass Same <string Script="Draw=Buffer;";> {
  ZEnable=false;ZWriteEnable=false;AlphaBlendEnable=false;CullMode=NONE;
  VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 First();
 }
 pass Same <string Script="Draw=Buffer;";> {
  ZEnable=false;ZWriteEnable=false;AlphaBlendEnable=false;CullMode=NONE;
  VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 Last();
 }
}
